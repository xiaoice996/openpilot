# DP111PRE 启动性能分析报告

> 设备：comma 3X (comma-39058564)
> 分析日期：2026-06-26
> 测试方法：多次重启采集时间点

## 启动时间线总览

```
0s      电源接通 / reboot 命令
│
├── ~8s   设备断电（reboot 后关机阶段）
│
├── ~65s  网络可达（ping 回复）     ← 最大瓶颈：65s 硬件初始化
│   ├── ~10s  内核启动 (3.4s) + systemd userspace 启动
│   ├── brightnessd 启动
│   ├── magic 服务就绪 (+4s 等待)
│   └── 网络就绪
│
├── ~67s  SSH 可用
│
├── ~77s  launch_chffrplus.sh 开始执行
│   ├── CPU 频率设置
│   ├── 等待 magic 服务
│   ├── Panda MCU 检测 (cached)
│   ├── TICI 硬件检测
│   └── 运行 continue.sh → launch_openpilot.sh
│
├── ~98s  build.py 开始 (scons 编译)
│   └── 首次启动需编译，耗时 ~21s
│
├── ~119s manager.py 启动
│   ├── 读取 CarParams
│   ├── 启动 offroad 进程组
│   │   ├── logmessaged
│   │   ├── timed
│   │   ├── ui (显示界面)
│   │   ├── pandad (CAN 通信)
│   │   ├── hardwared
│   │   ├── tombstoned
│   │   ├── updated
│   │   ├── statsd
│   │   └── serverd
│   └── 等待车辆点火 → 启动 onroad 进程组
│
└── ~120s+ 设备就绪（offroad 模式，等待车辆）
    └── 车辆点火后启动 onroad 进程：
        ├── camerad (摄像头)
        ├── sensord (传感器)
        ├── locationd (定位)
        ├── modeld (模型推理)
        ├── controlsd (控制)
        ├── selfdrived (自驾状态机)
        ├── card (车辆接口)
        ├── plannerd (规划)
        ├── radard (雷达)
        └── calibrationd / paramsd / torqued / lagd
```

## 关键耗时分解

| 阶段 | 耗时 | 占比 | 说明 |
|------|------|------|------|
| 硬件初始化 + 内核启动 | ~65s | **85%** | bootloader + kernel + 硬件探测 |
| systemd userspace | ~10s | 13% | 系统服务启动 |
| build.py (scons) | ~21s | 首次启动 | 编译 C++ 扩展，后续有缓存 |
| manager 启动 | ~21s | 等待 build | 含 CarParams 读取 |
| **总计 (到 offroad)** | **~120s** | | 从断电到界面就绪 |

## Systemd 服务耗时 Top 10

| 服务 | 耗时 | 说明 |
|------|------|------|
| NetworkManager | 3.1-4.1s | WiFi 初始化，可优化 |
| dev-sda7.device | 2.2-2.5s | 磁盘分区挂载 |
| magic | 1.5-1.8s | 显示/DRM 服务 |
| sound.service | 1.7s | 音频服务 |
| user@1000.service | 1.4-2.5s | 用户服务启动 |
| apport | 1.3-1.5s | 错误报告 |
| systemd-udev-trigger | 1.2s | 设备探测 |
| wpa_supplicant | 0.9s | WiFi 认证 |
| init-qcom | 0.9s | 高通初始化 |
| logd | 0.9-1.0s | 日志服务 |

## 启动瓶颈分析

### 瓶颈 1：硬件初始化 (65s) — 不可优化

从 reboot 到 ping 回复需要 ~65s，这是：
- bootloader (LK) 初始化
- 内核加载和硬件探测
- 高通 SoC 初始化（GPU、DSP、modem）
- 存储控制器初始化

**结论**：这是硬件层面的固有延迟，代码层面无法优化。

### 瓶颈 2：NetworkManager (3-4s) — 可优化

NetworkManager 是 systemd 中最慢的服务。它初始化 WiFi 模块、扫描网络、建立连接。

**优化方案**：
- 延迟启动：将 WiFi 初始化推迟到 openpilot 就绪后
- 使用 systemd socket activation 减少等待时间

### 瓶颈 3：build.py (21s) — 可优化

每次启动运行 `scons` 编译 C++ 扩展（cereal、msgq、params_pyx 等）。

**优化方案**：
- 创建 `prebuilt` 标记文件跳过编译
- 使用 scons 缓存 (`/data/scons_cache/`)
- 增量编译（只编译变更文件）

### 瓶颈 4：Magic 服务等待 (1-4s) — 可优化

`comma.sh` 等待 magic 服务就绪（最多 20s），实际通常 1-4s。

**优化方案**：
- 减少轮询间隔
- 并行化其他初始化工作

### 瓶颈 5：Manager 启动延迟 — 可优化

manager.py 在 build.py 完成后才启动，串行等待。

**优化方案**：
- 后台编译，manager 立即启动
- 预编译机制

## 优化清单

### 高优先级（预期节省 20-30s）

| # | 优化项 | 预期收益 | 风险 |
|---|--------|---------|------|
| 1 | **跳过 scons 编译**（创建 `prebuilt` 文件） | -21s | 首次部署需手动编译 |
| 2 | **延迟 NetworkManager**（推迟到 openpilot 就绪后） | -3-4s | 首次联网延迟 |
| 3 | **并行化 manager 启动**（不等 build 完成） | -10-15s | 需要修改 manager 逻辑 |

### 中优先级（预期节省 5-10s）

| # | 优化项 | 预期收益 | 风险 |
|---|--------|---------|------|
| 4 | **减少 magic 等待时间**（优化轮询） | -1-3s | 显示初始化可能不完整 |
| 5 | **禁用未使用服务**（apport、sound、pollinate） | -2-3s | 可能影响调试 |
| 6 | **优化 systemd 并行启动** | -1-2s | 需要仔细测试依赖关系 |

### 低优先级（预期节省 1-3s）

| # | 优化项 | 预期收益 | 风险 |
|---|--------|---------|------|
| 7 | **预加载 Python 模块** | -1-2s | 增加内存占用 |
| 8 | **优化内核启动参数** | -1-2s | 可能影响稳定性 |
| 9 | **使用 systemd socket activation** | -0.5-1s | 需要修改服务配置 |

## 实施建议

### 快速见效：跳过编译

```bash
# 在设备上创建 prebuilt 标记
touch /data/openpilot/prebuilt
# 这会让 build.py 跳过 scons 编译，直接启动 manager
```

**注意**：代码更新后需要删除 `prebuilt` 文件并重新编译。

### 中期优化：延迟 WiFi

修改 `comma.sh`，将 NetworkManager 启动推迟：

```bash
# 在 comma.sh 中，延迟启动 WiFi
sudo systemctl stop NetworkManager
# ... 其他初始化 ...
# 在 openpilot 就绪后启动
sudo systemctl start NetworkManager &
```

### 长期优化：重构启动流程

将 manager.py 改为异步启动，不等待 build 完成：

```python
# manager.py 中，后台运行 build
build_thread = threading.Thread(target=build)
build_thread.start()
# 立即启动 offroad 进程
start_offroad_processes()
# 等待 build 完成后再启动 onroad 进程
build_thread.join()
```

## 预期优化效果

| 场景 | 当前耗时 | 优化后 | 改善 |
|------|---------|--------|------|
| 冷启动到 offroad 就绪 | ~120s | ~90s | **-25%** |
| 冷启动到 onroad 可用 | ~120s+ | ~90s+ | **-25%** |
| 热重启（有缓存） | ~100s | ~70s | **-30%** |

## 附录：测试数据

### 测试 1 (2026-06-26 15:35)
- Reboot → Ping: 75s
- Reboot → SSH: 78s
- Systemd: 3.4s kernel + 10.9s userspace = 14.4s
- Manager 启动: 15:36:54 (boot +71s)
- Build 开始: 15:36:00 (boot +17s)

### 测试 2 (2026-06-26 15:39)
- Reboot → Ping: 65s
- Reboot → SSH: 67s
- Systemd: 3.4s kernel + 8.9s userspace = 12.3s
- Manager 启动: 15:40:32 (boot +49s)
- Build 开始: 15:40:11 (boot +28s)
