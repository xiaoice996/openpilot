# dp011 架构总览

> 面向第一次接手本项目的开发者 / AI：5 分钟搞懂这个 fork 是什么、怎么分层、数据怎么流动。
> 规则与红线见根目录 `CLAUDE.md`；上游 openpilot 通用概念见 `docs/` 其余文档。

## 这个项目是什么

dp011 是 **dragonpilot** 的单分支 fork —— 基于 openpilot（开源辅助驾驶系统）、面向华人用户的本地化定制版本。dragonpilot 由三位华人玩家于 2019 年创立，核心理念 **"Less is More"**：随上游 AI 模型增强，只在 `dragonpilot/` 目录做增量，尽量保留官方驾驶体验。最早引入完整中文界面，且唯一同时兼容 comma3/3X 与 O3 系列副厂硬件的社区分支。

| 项 | 值 |
|----|-----|
| 源仓库 | `https://jihulab.com/mr-one/openpilot.git`（分支 `dragonpilot`，可直连） |
| GitHub | `https://github.com/xiaoice996/openpilot.git`（分支 `dp011`，需代理） |
| 本地修复分支 | `dp011fix` / `dp011fix1`（启动加速 II 已推到 `dp011fix1`；见 `CLAUDE.md` 的"本地修改记录"） |

## 分层架构

```
┌─────────────────────────────────────────────────┐
│  dragonpilot/   ← 本地化定制层（dp_ 参数/中文UI/ALKA）│
├─────────────────────────────────────────────────┤
│  selfdrive/     ← 核心驾驶逻辑（感知→规划→控制→CAN） │
├─────────────────────────────────────────────────┤
│  system/        ← 设备基础设施（进程/硬件/采集/日志） │
├─────────────────────────────────────────────────┤
│  cereal + msgq  ← 通信总线（共享内存 pub/sub）       │
├─────────────────────────────────────────────────┤
│  panda + opendbc ← CAN 硬件/固件 + 车型 DBC         │
├─────────────────────────────────────────────────┤
│  common / rednose / tinygrad / third_party(acados)│
│  公共库 / 卡尔曼滤波 / 推理框架 / MPC求解 / 图形库    │
└─────────────────────────────────────────────────┘
```

## 各层职责

### 通信层（cereal + msgq）
- **cereal/**：消息定义（Cap'n Proto schema）。所有进程通过 pub/sub 通信，核心结构 `Event { logMonoTime, valid, union{各服务} }`。关键消息：`carState`、`modelV2`、`livePose`、`radarState`、`controlsState`、`sensorEvents`。
- **msgq/**（软链 → `msgq_repo/msgq`）：comma 自研的**共享内存环形缓冲 pub/sub**。每个服务 = 一块共享内存 = 1 个发布者 + 多订阅者，无锁原子指针，SIGUSR2 唤醒。
- ⚠️ **关键约束**：`logMonoTime` 用 `time.monotonic_ns()`（单调时钟）。传感器时间戳必须与其同基准，否则 locationd 丢弃数据——这是本项目 sensord 修复的根因，详见 `CLAUDE.md`。

### CAN 层（panda + opendbc）
- **panda/**：STM32H7 固件板，openpilot 与车辆 CAN 总线的物理桥接，在**固件层强制安全策略**（扭矩/加速度限幅、消息白名单）。即使上层软件崩溃也不会失控——硬件级安全边界。
- **opendbc/**（软链 → `opendbc_repo/opendbc`）：车型 DBC 文件库 + 车型接口代码（14 品牌）。`CANParser` 解码接收、`CANPacker` 编码发送（自动处理 counter/checksum）。

### system 层（基础设施）
- **managerd**（`system/manager/`）：进程管理器，按 `process_config.py` 注册表 + `should_run` 谓词启停所有守护进程，是系统启动入口。
- **sensord / camerad / ubloxd / micd**：IMU / 相机 / GPS / 麦克风采集。
- **loggerd / encoderd**：所有 cereal 消息落盘成路段（qlog/rlog），训练数据与回放调试的根基。
- **tombstoned / proclogd / hardwared / updated / athenad**：崩溃捕获 / 指标 / 温控电源 / OTA / 远程云端。

### selfdrive 层（核心驾驶逻辑）
新版架构把旧 `controlsd` 拆成多个独立守护进程，职责分离，仅靠消息总线通信：

| 进程 | 频率 | 职责 |
|------|------|------|
| **card** | 100Hz | CAN↔CarState 转换，车型指纹识别，执行器输出 |
| **selfdrived** | 100Hz | 监督层：聚合信号→计算事件→状态机→告警 |
| **controlsd** | 100Hz | 横向（PID/torque/angle）+ 纵向（PID）控制器 |
| **plannerd** | 20Hz | 纵向 MPC 规划 |
| **radard** | 20Hz | 雷达+视觉融合，输出 lead 前车 |
| **modeld** | 20Hz | tinygrad 跑驾驶模型（vision+policy） |
| **dmonitoringmodeld / dmonitoringd** | 20Hz | 驾驶员监控（分心/闭眼/离岗） |
| **locationd** | 20Hz | PoseKalman 融合 IMU+posenet → 位姿 |
| **calibrationd / paramsd / torqued / lagd** | 4Hz | 安装角标定 / 车辆参数学习 / 扭矩学习 / 延迟估计 |
| **pandad** | always | panda 通信桥接 + safety 切换 |

### dragonpilot 定制层
**不是独立进程，而是"侵入式注入"**：修改上游 `selfdrive/`、`system/` 源码，在其中 import `dragonpilot` 模块。定制代码集中在 `dragonpilot/`，只新增 3 个守护进程：`beepd`（GPIO 蜂鸣）、`dashyd`（15Hz 状态聚合 JSON）、`serverd`（aiohttp 网页后端，手机访问）。

定制通过 `dp_*` 参数 + `DPFlags` 位标志（`opendbc_repo/opendbc/car/structs.py`）贯穿 card→selfdrived→controlsd→plannerd→modeld 全链路。功能分横向（ALKA/LCA/偏移/路缘）、纵向（ACM/AEM/APM）、UI（中文/彩虹路径）、设备（自动关机/延迟录制）、品牌专属（丰田/VAG/Honda Nidec）。

构建期：`generate_settings.py` 用 AST 扫描 `dragonpilot/settings/*.py`，把 `dp_*` 参数合并进 `common/params_keys.h`，实现"每功能一文件、合并不冲突"。详见 `dragonpilot/settings_management.md`。ALKA 设计详见根目录 `ALKA_DESIGN.md`。

## 整体数据流

```
传感器/CAN → modeld(感知) + locationd(定位) + radard(前车)
          → selfdrived(决策状态机) → plannerd(纵向规划)
          → controlsd(横纵向控制 → carControl) → card(CAN帧 → sendcan)
          → pandad → panda固件(safety校验) → 车辆执行机构
```

- **频率分层**：100Hz 控制环路（card/controlsd/selfdrived）→ 20Hz 感知规划（modeld/plannerd/radard/locationd）→ 4Hz 慢学习（calibrationd/torqued/lagd）。
- **在线学习**：paramsd/torqued/lagd 持续学习车辆参数，缓存到 params，跨行程复用。
- **安全闭环**：panda 固件层独立做 safety 约束；selfdrived 持续校验 panda safety model 与 CarParams 一致。

## 本地修复

本 fork 在 `dp011fix` / `dp011fix1` 分支做了硬件适配与启动加速修复，**规则与详细根因见根目录 `CLAUDE.md` 的"本地修改记录"**：

1. **sensord 轮询模式**：GPIO 84 数据就绪中断不触发 → IMU 从中断改轮询；时间戳必须用 `time.monotonic_ns()` 对齐 `logMonoTime`，否则 locationd 丢弃全部 IMU 数据。
2. **sensorDataInvalid 误报屏蔽**：屏幕 I2C 硬件错误导致超时误判 → 注释超时检测逻辑。
3. **scons 字体规则修复**：`selfdrive/ui/SConscript` 字体 Command 的 target 推导对 `OpFont-*.otf` 与 `unifont.otf` 错误，导致每次启动重跑 `process.py`（~24s）；穷举真实输出后启动耗时 64s → 42s。
4. **prebuilt + git hook**（设备配置，非源码改动）：`/data/openpilot/prebuilt` 标记跳过 `build.py` 整段；post-merge/post-checkout hook 在代码变更后自动删除标记触发重编。启动耗时 42s → 26.5s。

> 升级 openpilot 版本时这些修复可能需要重新应用。

## 构建与运行

- **构建**：SCons 构建原生 C/C++ 守护进程 + Cython 编译 `.pyx`；依赖由 `pyproject.toml` + `uv.lock` 管理（Python 3.12）。
- **PC 开发**：`tools/sim/`（MetaDrive 模拟器）或 `tools/webcam/`（本机摄像头），tinygrad 走 CPU 后端。
- **tici 设备**：`launch_openpilot.sh` → `launch_chffrplus.sh` → `managerd` 拉起所有进程，tinygrad 走 QCOM Adreno GPU。

### tici 启动时序

通电后启动链路（实测：内核+systemd ~14s，正常）：

1. systemd 拉起 `comma.service` → `/usr/comma/comma.sh`（等 `magic` 显示服务就绪）
2. `launch_chffrplus.sh`：`agnos_init`（AGNOS 版本校验）→ overlay 更新检查（若 `/data/safe_staging/finalized/.overlay_consistent` 存在则安装 overlay）→ `set_tici_hw`（查询 panda MCU 区分 DOS/TRES）→ 挂载 NVMe
3. `system/manager/build.py` 跑 `scons`：**默认每次启动都执行**（按 mtime 增量编译）。本 fork 通过 `/data/openpilot/prebuilt` 标记跳过整段，配合 git hook 在 pull/merge/切分支后自动清除标记触发重编——详见 `CLAUDE.md` 的"启动加速 III"
4. `managerd`：preimport 所有进程模块 → 按 `should_run` 拉起守护进程 → onroad 后启动 card/controlsd/modeld 等

**启动慢的已知坑**：`updated` 进程（offroad 运行）做 OTA 时 `git reset` 会刷新所有源文件 mtime，导致下次启动 scons 全量重编。本 fork 通过 `DisableUpdates=1` 禁用 OTA 规避——设备配置与效果见根目录 `CLAUDE.md` 的"启动加速"条目。

## 相关文档

- `CLAUDE.md` — 项目规则、红线、本地修改记录（sensord / sensorDataInvalid）
- `ALKA_DESIGN.md` — ALKA 全时车道维持设计（含各品牌 ACC Main 信号源）
- `dragonpilot/settings_management.md` — dp 设置合并机制（generate_settings.py）
- `docs/SAFETY.md`、`docs/INTEGRATION.md`、`docs/getting-started/` — 上游 openpilot 通用文档
- `cereal/README.md`、`msgq_repo/README.md`、`panda/README.md` — 各子组件 README
