# dp011 项目指南

## 项目概述

dp011 是 [dragonpilot](https://jihulab.com/mr-one/openpilot.git) 的单分支 fork，基于 openpilot 开源辅助驾驶系统，面向华人用户的本地化定制版本。

## 仓库信息

| 项目 | 值 |
|------|-----|
| 源仓库 | `https://jihulab.com/mr-one/openpilot.git` (分支 `dragonpilot`) |
| GitHub | `https://github.com/xiaoice996/openpilot.git` (基线 `dp011`，工作分支 `dp011fix` → `dp011fix1`) |
| 克隆方式 | `--depth=1` 浅克隆 |

## 网络配置

GitHub 在国内无法直连，所有 git 操作需加代理：

```bash
git -c http.proxy=http://127.0.0.1:7890 -c http.version=HTTP/1.1 push/pull/clone
```

jihulab（极狐 GitLab）可直连，无需代理。

## 深入文档

接手项目时优先读这些（CLAUDE.md 只放规则，机制细节在 docs）：

- `docs/ARCHITECTURE.md` — 本 fork 架构总览：分层、各进程职责、数据流、dragonpilot 注入机制
- `ALKA_DESIGN.md` — ALKA 全时车道维持设计（各品牌 ACC Main 信号源）
- `dragonpilot/settings_management.md` — dp 设置合并机制（generate_settings.py）

## 本地修改记录

### sensorDataInvalid 误报屏蔽

- **文件**: `selfdrive/selfdrived/selfdrived.py:359-362`
- **原因**: 屏幕 I2C 硬件错误导致 accelerometer/gyroscope 数据接收超时误判，触发 "Sensor Data Invalid - Possible Hardware Issue" 并 SOFT_DISABLE 系统
- **处理**: 注释掉传感器数据超时检测逻辑
- **Commit**: `19e8611` (2026-06-15)
- **注意**: 升级 openpilot 版本时可能需要重新应用此修改

### sensord GPIO 中断修复 — 切换为轮询模式

- **文件**:
  - `system/sensord/sensord.py` — accelerometer/gyroscope 从中断模式切换为轮询模式，条件启动 interrupt_loop
  - `system/sensord/sensors/lsm6ds3_accel.py` — get_event() 支持无时间戳调用（轮询模式）
  - `system/sensord/sensors/lsm6ds3_gyro.py` — get_event() 支持无时间戳调用（轮询模式）
- **原因**: GPIO 84 数据就绪中断不触发（`poll timed out`），导致 sensord 无法发布 accelerometer/gyroscope 数据，进而 locationd/paramsd/lagd/torqued 全部输出无效数据，校准完成后触发 "Communication Issue Between Processes" SOFT_DISABLE
- **处理**:
  1. 将 accelerometer 和 gyroscope 的中断标志从 `True` 改为 `False`，使用 polling_loop 替代 interrupt_loop
  2. 仅在有传感器需要中断时才启动 interrupt_loop
  3. 传感器 get_event() 在 ts=None 时使用 `time.monotonic_ns()` 作为时间戳（必须用 monotonic_ns，否则与 messaging 的 logMonoTime 基准不一致，locationd 会丢弃所有传感器数据）
  4. polling_loop 中 DataNotReady 异常静默处理（轮询模式下正常现象）
- **Commit**: `26eb8f0` (分支 `dp011fix`, 2026-06-16)
- **注意**: 升级 openpilot 版本时需要重新应用此修改。若 GPIO 84 中断恢复，可改回 `True` 以获得更低延迟

## 启动加速 — 禁用 OTA 避免 scons 全量重编

- **现象**:设备从通电到驾驶模式可用耗时 ~7 分钟(openpilot 在内核启动后 +415 秒才启动)
- **根因**:`updated` 进程(offroad 运行)做 OTA 时 `git reset` 刷新所有源文件 mtime → 下次启动 `build.py` 的 scons 判定全量过期,重编 loggerd/camerad 等 C++ 守护进程(~6 分钟)。`scons -n` dry-run 证明 mtime 稳定时增量仅 14 秒
- **处理**(设备持久配置,非源码改动):
  1. `DisableUpdates` param 保持 `1`(`updated.py:428` 入口检查后 `exit(0)`,失去自动 OTA;手动更新用 `git pull` + 下次启动自动增量编译)
  2. 清理 `/data/safe_staging/finalized/.overlay_consistent`(否则 `launch_chffrplus.sh` 的 `launch()` 会安装 overlay 再次刷新 mtime 触发重编)
  3. 清 `UpdateAvailable`/`UpdaterState` param;删 NetworkManager 残留 WiFi Direct 连接减少扫描卡顿
- **效果**:openpilot 启动耗时 ~415 秒 → **64 秒**(2026-06-21)
- **注意**:重置设备或换设备后需重新设置 `DisableUpdates=1`。手动 `git pull` 更新代码后,下次启动 build.py 会自动增量编译,无需额外操作

## 启动加速 II — 修复 scons 字体生成规则的虚假重跑

- **文件**: `selfdrive/ui/SConscript`(第 6-28 行替换字体 Command 的 target 推导)
- **根因**: 原 SConscript 把每个源字体 `Foo.{ttf,otf}` 的目标推为同名 `Foo.{fnt,png}`。这对 `Inter-*`/`JetBrainsMono-*` 正确,但对 `OpFont-*.otf` 和 `unifont.otf` 错误:
  - `OpFont-*.otf` 实际生成 `OpFont-{weight}-Labels.{fnt,png}` 和 5 个 unifont 语言(th/zh-CHT/zh-CHS/ko/ja)的 atlas,从不生成同名 `OpFont-{weight}.{fnt,png}`
  - `unifont.otf` 在 `process.py:151` 被显式跳过,从不生成任何输出
  - 结果:scons 每次都判定这些 target 缺失 → 整体重跑 `process.py`(~24 秒)
- **处理**: 把 OpFont-* 的 6 个真实输出穷举出来,并把 `unifont.otf` 加入跳过列表
- **效果**: `scons -j6 --minimal` 增量耗时 **47s → 14.7s**;openpilot 启动耗时 **64s → 42s**(节省 22s,实测 2026-06-22)
- **Commit**: `a517888` (分支 `dp011fix1`,GitHub 已推送)
- **注意**: 若上游修改 `process.py:UNIFONT_LANGUAGES` 或新增/删除 `OpFont-*` 字体,需同步更新 SConscript 中的 `_UNIFONT_LANGS` 和 `_SKIP_NAMES`

## 启动加速 III — prebuilt 标记跳过 scons + git hook 反向阀门

- **现象**: 即使字体规则已修, `build.py` 每次启动仍跑 ~15s scons (Python 启动 + SConstruct 解析 + generate_settings + compile_commands.json 重建), 实际无任何编译工作
- **根因**: `launch_chffrplus.sh:159` 有官方设计 `if [ ! -f $DIR/prebuilt ]; then ./build.py; fi` —— 存在 `prebuilt` 标记即跳过 build.py。comma 出厂镜像就用这个机制。但手动维护风险:`git pull` 改了 C++ 源码后,prebuilt 仍在 → 启动时跑旧二进制
- **处理**:
  1. 设备 `/data/openpilot/prebuilt` 标记文件 (touch 即可,内容空)
  2. **反向阀门**(关键):git hook `post-merge` 和 `post-checkout`(branch_flag=1) 自动删除 prebuilt
     - 文件: `/data/openpilot/.git/hooks/post-merge` 和 `post-checkout`(都需 `chmod +x`)
     - post-checkout 必须判 `$3=1` 才删, 否则 `git checkout <file>` 单文件会误删
     - hooks 不受 git 追踪, 设备本地配置
- **效果**: openpilot 启动耗时 **42s → 26.5s**(节省 15.5s,实测 2026-06-21);累计相对原始 415s 已 **16 倍加速**
- **注意**:
  - 重置设备 / 换设备后需重新建 prebuilt 文件 + 重新配置 hooks
  - 任何直接 `rm /data/openpilot/prebuilt` 或代码改动经 git pull/merge/checkout 后, 下次启动会自动重编(~15s 一次性开销)
  - 若 hook 失效或被绕过, 需手动 `rm prebuilt` 后再启动, 否则跑旧二进制风险

## 项目结构速查

```
dp011/
├── selfdrive/          # 核心驾驶逻辑
│   ├── selfdrived/     # 主控制循环
│   ├── car/            # 各车型适配
│   └── assets/         # 资源文件
├── dragonpilot/        # dragonpilot 定制功能
├── cereal/             # 消息序列化
├── panda/              # CAN 总线接口
├── opendbc/            # 车型 DBC 文件
└── docs/               # ARCHITECTURE.md（本 fork 总览）+ 上游 openpilot 文档
```
