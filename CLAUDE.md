# Dragonpilot 项目指南

基于 openpilot 0.11.1 的社区分支，为华人用户提供本地化功能。核心理念：最小化修改上游代码，通过动态注入实现功能扩展。

## 构建与测试

```bash
# 构建（SCons，自动运行 generate_settings.py 生成 params_keys.h）
scons -j$(nproc)

# 测试
pytest -x selfdrive/                    # selfdrive 测试
pytest -x common/                       # common 测试
pytest -x -k "not slow and not tici"    # 跳过慢速和设备测试

# 生成设置（通常由 SConstruct 自动调用）
python generate_settings.py
```

## 架构速查

```
CAN 总线 → pandad → card.py (100Hz) → controlsd (100Hz) → carControl → card.py → CAN
                              ↓
摄像头 → modeld (20Hz) → modelV2 → plannerd → longitudinalPlan → controlsd
```

**进程管理**：`system/manager/process_config.py` 定义 40+ 受管进程，`manager.py` 根据点火状态启停。

**通信层**：cereal (Cap'n Proto) + msgq (pub/sub)，服务定义见 `cereal/services.py`。

**车辆接口**：`opendbc_repo/opendbc/car/<brand>/` 下每个品牌有 interface.py / carstate.py / carcontroller.py / fingerprints.py。

## 关键集成点（DP 对上游的修改）

DP 核心功能只改 3 个文件，其余全部通过动态 import 注入：

1. **`selfdrive/car/card.py`** — 读取 DP params 构建 DPFlags 位掩码，传给 `get_car()`；发布 `carStateExt`（含 `lkas_on`）
2. **`selfdrive/controls/controlsd.py`** — 集成 HTD（人机转向检测）+ ALKA（常用车道保持）；发布 `controlsStateExt`
3. **`selfdrive/controls/lib/longitudinal_planner.py`** — 编排 DP 纵向模块（APM/AEM/OCM/DTSC/AccelController）

## DP 功能模块注入方式

所有 DP 功能在 `dragonpilot/selfdrive/controls/lib/` 下，通过运行时动态 import 注入：

```python
# 示例：radard.py 末尾
from dragonpilot.selfdrive.controls.radard_ext import RadarDExt
# 示例：longitudinal_planner.py
from dragonpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlannerDP
```

这样上游代码改动时，DP 模块只需适配 import 接口，不产生合并冲突。

## 设置系统

详见 `dragonpilot/settings_management.md`。

- 每个 feature 分支在 `dragonpilot/settings/` 下放一个 `.py` 文件，定义 `ITEMS` 列表
- 构建时 `generate_settings.py` 扫描所有文件，生成 `common/params_keys.h`
- 参数键以 `dp_` 前缀命名，分类：`dp_lat_*`（横向）、`dp_lon_*`（纵向）、`dp_toyota_*`/`dp_vag_*`/`dp_honda_*`（品牌）、`dp_ui_*`（UI）、`dp_dev_*`（开发者）
- **不要手动编辑 `common/params_keys.h`**，它是构建产物

## 设备特定修改

除 DP 核心 3 文件外，以下文件因设备硬件问题（GPIO 中断失效、启动慢）被修改，详见 `docs/DP111PRE_DEVICE_FIXES.md`：

- `system/sensord/sensord.py` — accel/gyro 改用 polling 模式（`interrupt=True→False`）
- `system/sensord/sensors/lsm6ds3_accel.py` / `lsm6ds3_gyro.py` — `get_event()` 支持 `ts=None`
- `selfdrive/selfdrived/selfdrived.py` — 文件检查跳过 sensorDataInvalid 误报
- `system/manager/manager.py` — 并行化模块预导入
- `launch_chffrplus.sh` — 屏蔽非必要 systemd 服务 + Quick Start 开关控制 scons 编译
- `selfdrive/pandad/pandad.py` — `get_expected_signature()` 固件缺失/损坏（<128B）时自动 scons 构建（迁移自 3xl 分支，防 Quick Start 跳过编译后 pandad 启动失败）；主循环 reset 后等待 panda 回到正常模式（`bootstub=False`）再决定是否刷写，不再交替调用 `recover_internal_panda()`（修复开机白刷固件、上线 41s→7s）
- **设备端**：`echo '1' > /data/params/d/dp_dev_quick_start` 跳过 scons 编译（设置面板 → Device → Quick Start）

**注意**：升级 openpilot 后这些修改会被覆盖，需重新应用。

## 红线

- **不手动编辑生成文件**：`common/params_keys.h` 由 `generate_settings.py` 生成
- **不破坏 panda 安全层**：panda 固件（`panda/board/`）的 safety 检查是最后防线
- **DP 修改集中在 3 个文件**：新增集成点需要充分理由
- **动态 import 优先**：新 DP 功能通过 `dragonpilot/` 下的模块注入，不直接改上游文件
- **密钥/token 不进代码**：panda 证书在 `panda/board/certs/`，不要动

## 深入文档

| 文档 | 位置 |
|------|------|
| DP 设置系统架构 | `dragonpilot/settings_management.md` |
| ALKA 设计文档 | `ALKA_DESIGN.md` |
| 分支层级结构 | `BRANCHES.md` |
| 变更日志 | `CHANGELOGS.md` |
| 版本发布说明 | `RELEASES.md` |
| 支持车型列表 | `docs/CARS.md` |
| 开发环境搭建 | `docs/DEVELOPMENT.md` |
| 安全说明 | `docs/SAFETY.md` |
| 上游 openpilot README | `README_OPENPILOT.md` |
| 设备修复记录 | `docs/DP111PRE_DEVICE_FIXES.md` |
| pandad 签名读取与上线慢修复 | `docs/pandad_issue_and_fix.md` |
| 启动性能分析 | `docs/BOOT_ANALYSIS.md` |
