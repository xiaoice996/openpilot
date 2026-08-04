# DP111PRE 设备修改汇总

> 设备：comma 3X (comma-39058564)
> 修改日期：2026-06-26（传感器修复 / 启动优化）；2026-08-04（panda 固件自动构建 + 上线慢修复）
> 适用分支：dp111pre

---

## 一、传感器硬件修复（GPIO 中断失效）

### 问题

屏幕 I2C 硬件异常导致 GPIO 84 数据就绪中断不触发（IRQ 336 计数为 0），sensord 无法通过中断模式发布 accel/gyro 数据，引发级联故障：

```
GPIO 84 中断不触发
  → sensord interrupt_loop 无限等待
    → accelerometer / gyroscope 无数据
      → locationd 无输入 → livePose / liveDelay 无效
      → paramsd / torqued 异常 → liveParameters / liveTorqueParameters 无效
        → selfdrived SubMaster.all_checks() 失败
          → "传感器数据无效" + "程序通讯故障"
```

### 修改文件

#### 1. `system/sensord/sensors/lsm6ds3_accel.py`

**路径**：`/data/openpilot/system/sensord/sensors/lsm6ds3_accel.py`
**参考**：commit `26eb8f0` (分支 `dp011fix`, 2026-06-16)

`get_event()` 原本要求 `ts` 由 IRQ 提供（`assert ts is not None`）。改为 `ts=None` 时使用 `time.monotonic_ns()`，适配 `polling_loop` 调用。

```python
# 修改前
def get_event(self, ts: int | None = None) -> log.SensorEventData:
    assert ts is not None  # must come from the IRQ event

# 修改后
def get_event(self, ts: int | None = None) -> log.SensorEventData:
    if ts is None:
      ts = time.monotonic_ns()
```

#### 2. `system/sensord/sensors/lsm6ds3_gyro.py`

**路径**：`/data/openpilot/system/sensord/sensors/lsm6ds3_gyro.py`
**参考**：commit `26eb8f0` (分支 `dp011fix`, 2026-06-16)

同 accel，`get_event()` 支持 `ts=None`。

```python
# 修改前
def get_event(self, ts: int | None = None) -> log.SensorEventData:
    assert ts is not None  # must come from the IRQ event

# 修改后
def get_event(self, ts: int | None = None) -> log.SensorEventData:
    if ts is None:
      ts = time.monotonic_ns()
```

#### 3. `system/sensord/sensord.py`

**路径**：`/data/openpilot/system/sensord/sensord.py`
**参考**：commit `26eb8f0` (分支 `dp011fix`, 2026-06-16)

将 accel/gyro 的中断标志从 `True` 改为 `False`，使其使用 `polling_loop`（I2C 轮询）而非 `interrupt_loop`（GPIO 中断）。

```python
# 修改前
sensors_cfg = [
    (LSM6DS3_Accel(I2C_BUS_IMU), "accelerometer", True),
    (LSM6DS3_Gyro(I2C_BUS_IMU), "gyroscope", True),
    (LSM6DS3_Temp(I2C_BUS_IMU), "temperatureSensor", False),
]

# 修改后
sensors_cfg = [
    (LSM6DS3_Accel(I2C_BUS_IMU), "accelerometer", False),
    (LSM6DS3_Gyro(I2C_BUS_IMU), "gyroscope", False),
    (LSM6DS3_Temp(I2C_BUS_IMU), "temperatureSensor", False),
]
```

#### 4. `selfdrive/selfdrived/selfdrived.py`

**路径**：`/data/openpilot/selfdrive/selfdrived/selfdrived.py`
**参考**：commit `19e8611` (2026-06-15)

当 `/data/params/d/dp_dev_ignore_sensor_check` 文件存在时，跳过 `sensorDataInvalid` 事件。

```python
# 修改前
    # conservative HW alert. if the data or frequency are off, locationd will throw an error
    if any((self.sm.frame - self.sm.recv_frame[s])*DT_CTRL > 10. for s in self.sensor_packets):
      self.events.add(EventName.sensorDataInvalid)

# 修改后
    # conservative HW alert. if the data or frequency are off, locationd will throw an error
    # dp: skip this check when screen I2C hardware causes false positives
    import os
    if not os.path.exists("/data/params/d/dp_dev_ignore_sensor_check"):
      if any((self.sm.frame - self.sm.recv_frame[s])*DT_CTRL > 10. for s in self.sensor_packets):
        self.events.add(EventName.sensorDataInvalid)
```

#### 5. `dragonpilot/settings/min-feat.dev.ignore-sensor-check.py`（新建）

**路径**：`/data/openpilot/dragonpilot/settings/min-feat.dev.ignore-sensor-check.py`

注册 `dp_dev_ignore_sensor_check` 布尔开关，DP 设置面板 Device 分类下显示。

```python
from dragonpilot.settings import tr

ITEMS = [
  {
    "section": "Device",
    "key": "dp_dev_ignore_sensor_check",
    "type": "toggle_item",
    "title": lambda: tr("Ignore Sensor Check"),
    "description": lambda: tr("Ignore sensor data invalid alert caused by screen I2C hardware issue."),
    "flags": "PERSISTENT",
    "param_type": "BOOL",
    "default": "0",
  },
]
```

#### 6. `common/params_keys.h`（自动生成）

**路径**：`/data/openpilot/common/params_keys.h`

由 `generate_settings.py` 自动生成，包含 `dp_dev_ignore_sensor_check`。**不要手动编辑**。

### 设备端 Param

```bash
# 开启（跳过传感器检查）
echo '1' > /data/params/d/dp_dev_ignore_sensor_check

# 关闭（恢复原始行为）
echo '0' > /data/params/d/dp_dev_ignore_sensor_check
```

---

## 二、启动性能优化

### 问题

从断电到 openpilot 就绪需要 ~120s，其中：
- 硬件初始化：65s（不可优化）
- scons 编译：21s（可跳过）
- manager 启动：18s（可优化）

### 修改文件

#### 1. 跳过 scons 编译（Quick Start 开关）

**路径**：`/data/openpilot/launch_chffrplus.sh`

通过 `dp_dev_quick_start` 开关控制是否跳过 `build.py`。开启时自动创建 `prebuilt` 标记文件，关闭时删除。

```bash
# launch_chffrplus.sh 中的逻辑
if [ -f /data/params/d/dp_dev_quick_start ] && [ "$(cat /data/params/d/dp_dev_quick_start)" = "1" ]; then
  touch $DIR/prebuilt
else
  rm -f $DIR/prebuilt
fi
```

**设置文件**：`dragonpilot/settings/min-feat.dev.quick-start.py`

```bash
# 设备端手动控制
echo '1' > /data/params/d/dp_dev_quick_start  # 开启（跳过编译）
echo '0' > /data/params/d/dp_dev_quick_start  # 关闭（正常编译）
```

**注意**：代码更新后需关闭此开关并重启，让 scons 重新编译。

#### 2. `launch_chffrplus.sh` — 屏蔽非必要服务

**路径**：`/data/openpilot/launch_chffrplus.sh`

在启动脚本开头添加 systemd 服务屏蔽，减少启动耗时。

```bash
# 在 'source "$DIR/launch_env.sh"' 之后添加：
# dp_optimization: mask non-essential services to speed up boot
sudo systemctl mask --runtime apport.service 2>/dev/null &
sudo systemctl mask --runtime sound.service 2>/dev/null &
sudo systemctl mask --runtime pollinate.service 2>/dev/null &
```

#### 3. `system/manager/manager.py` — 并行化模块预导入

**路径**：`/data/openpilot/system/manager/manager.py`

将 `manager_init()` 中的模块预导入从串行改为并行（4 线程）。

```python
# 修改前
  # preimport all processes
  for p in managed_processes.values():
    p.prepare()

# 修改后
  # preimport all processes (dp_optimization: parallelize imports)
  from concurrent.futures import ThreadPoolExecutor, as_completed
  with ThreadPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(p.prepare): p.name for p in managed_processes.values() if p.enabled}
    for future in as_completed(futures):
      name = futures[future]
      try:
        future.result()
      except Exception:
        cloudlog.exception(f"failed to prepare {name}")
```

### 优化效果

| 指标 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| 总启动时间 | ~120s | ~70s | **-42%** |
| Manager 启动 | boot +49s | boot +18s | **-63%** |

---

## 三、验证方法

```bash
# SSH 到设备
ssh -i "/Users/xiaoice/Downloads/密钥/id_ed25519" comma@<IP>

# 验证传感器数据流动（比较原始字节，mmap 不更新时间戳）
head -c 16 /dev/shm/msgq_accelerometer | od -A x -t x1
sleep 1
head -c 16 /dev/shm/msgq_accelerometer | od -A x -t x1
# 字节变化 = 数据正常

# 验证关键服务发布
for s in livePose liveDelay liveParameters liveTorqueParameters; do
  head -c 16 /dev/shm/msgq_$s | od -A x -t x1
done

# 验证启动时间
systemd-analyze time
ps -eo pid,lstart,cmd --sort=lstart | grep manager | head -2

# 验证 Quick Start 开关
cat /data/params/d/dp_dev_quick_start 2>/dev/null || echo "not set"
ls -la /data/openpilot/prebuilt 2>/dev/null || echo "no prebuilt"

# 验证服务屏蔽
systemctl list-unit-files | grep masked-runtime
```

## 四、回滚方法

```bash
cd /data/openpilot

# 回滚传感器修复
git checkout system/sensord/sensord.py
git checkout system/sensord/sensors/lsm6ds3_accel.py
git checkout system/sensord/sensors/lsm6ds3_gyro.py
git checkout selfdrive/selfdrived/selfdrived.py
rm dragonpilot/settings/min-feat.dev.ignore-sensor-check.py
rm /data/params/d/dp_dev_ignore_sensor_check

# 回滚启动优化
rm /data/openpilot/prebuilt
rm dragonpilot/settings/min-feat.dev.quick-start.py
git checkout system/manager/manager.py
git checkout launch_chffrplus.sh
git checkout selfdrive/pandad/pandad.py

# 重新生成 params_keys.h
python3 generate_settings.py

# 重启
sudo reboot
```

## 五、已知限制

- **GPIO 中断未修复**：根因是硬件层面 GPIO 84 信号未到达处理器，代码层面只能用 polling 绕过
- **prebuilt 由 Quick Start 开关控制**：代码更新后需关闭 `dp_dev_quick_start` 并重启
- **服务屏蔽为 runtime**：重启后失效，已写入 `launch_chffrplus.sh` 每次启动时重新应用
- **升级时需重新应用**：所有修改在 openpilot 升级后会被覆盖

---

## 六、panda 固件自动构建（迁移自 3xl 分支，2026-08-04）

### 问题

Quick Start 开关（`dp_dev_quick_start`）跳过 scons 编译时，`panda/board/obj/` 固件可能缺失，`selfdrive/pandad/pandad.py` 的 `get_expected_signature()` 会因文件不存在抛异常，pandad 循环失败无法接管车辆。

### 修改文件

`selfdrive/pandad/pandad.py` — `get_expected_signature()` 在固件缺失或 <128 字节时，用 `scons -C BASEDIR <target>` 现场构建 `panda_h7.bin.signed` 后再取签名（构建失败抛异常，由 `main()` 捕获重试）。

### 来源与提交

- 3xl 分支（sunnypilot，jihulab mr-one/openpilot）提交 `fd4701e`（2026-08-04）
- 迁移提交 `fd27160`，本地分支 `dp111preup1`，已推送 https://github.com/xiaoice996/openpilot.git
- **后续补充**：pandad 开机慢问题（每次开机白刷固件）及主循环等待修复见 `docs/pandad_issue_and_fix.md`，修复已应用到 `selfdrive/pandad/pandad.py`（见下文第七节）
- **未迁移项**：`get_type()` 硬编码返回 TRES（安全 hack，不建议）、固件产物入库（与 Quick Start 机制冲突）

### 回滚

`git checkout selfdrive/pandad/pandad.py`

---

## 七、pandad 开机上线慢修复（等待 panda 正常启动后再决定刷写，2026-08-04）

### 问题

设备每次开机后 "Panda online" 需要约 41 秒，实测 pandad wrapper 启动到真正 pandad 上线约 41 秒，且每次开机 panda 都被判定为 bootstub 并重复刷写固件。

### 根因

- GPIO reset 后内部 panda 需要约 **5.5 秒** 才进入正常固件。
- `recover_internal_panda()` 会把 panda 强制送进 bootloader，实测 **20 秒以上** 无法恢复。
- 旧 pandad 在 reset 后立即查询，看到 panda 还在 bootstub 就判定固件过期并执行刷写（约 9 秒）。
- `PandaDFU.list()` 在 panda 启动过程中还会阻塞约 7 秒。

### 修改文件

`selfdrive/pandad/pandad.py` — 主循环：

1. **只执行 `HARDWARE.reset_internal_panda()`**，不再交替调用 `recover_internal_panda()`（避免把 panda 送进 bootloader）。
2. **reset 后轮询等待**（每 0.5s 一次，最多 16 次 ≈ 8s）：`Panda.list()` 能查到 panda 且 `bootstub=False`（正常模式）才继续，避免在 panda 启动过程中误判为 bootstub 而刷写。
3. 只有 panda 确实等不到（仍在 bootstub 或 DFU）时才执行 DFU 恢复；若 panda 真是 bootstub 或签名不一致，仍走原有 `flash_panda()` 正常刷写逻辑。

`get_expected_signature()`（含 Quick Start 跳过编译时的 scons 自动构建）保持原样，不做修改。

### 验证结果（设备实测）

| 项目 | 修复前 | 修复后 |
| --- | --- | --- |
| pandad wrapper 到真正 pandad | 约 41 秒 | 约 7 秒 |
| panda 状态 | 每次开机 bootstub 并刷写 | 正常固件，无重复刷写 |
| 签名读取 | 保持原样 | 保持原样，不做修改 |

详见 `docs/pandad_issue_and_fix.md`。

### 回滚

`git checkout selfdrive/pandad/pandad.py`
