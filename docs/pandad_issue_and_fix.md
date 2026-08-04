# pandad 开机签名读取与 Panda 上线慢问题记录

## 问题

1. 设备开机后 `get_expected_signature()` 获取不到固件签名，返回空。
2. 每次开机后 “Panda online” 需要很长时间，实测 pandad wrapper 启动到真正 pandad 上线约 41 秒。

## 根因

### 1. 固件签名文件缺失

`get_expected_signature()` 读取的固件文件是：

```text
<openpilot>/panda/board/obj/panda_h7.bin.signed
```

该文件是构建产物，被 `.gitignore` 忽略：

```text
panda/.gitignore: obj/
```

overlay 更新树 `/data/safe_staging/finalized/panda/board/obj/` 里没有该文件，只有 `.placeholder`；
`/data/safe_staging/upper/panda/board/obj/panda_h7.bin.signed` 是 overlay whiteout。

因此开机走 overlay 更新后，实际读取路径相同，但文件不存在：

- 旧版 `get_signature_from_firmware()` 直接 `open()`，会抛 `FileNotFoundError`。
- 另一份代码里 `get_expected_signature()` 用 `except Exception` 吞掉了异常并返回 `b""`，表现为“返回空”。

### 2. pandad 启动逻辑导致每次开机都白刷固件

实际测试结果：

- GPIO reset 后，内部 panda 大约需要 **5.5 秒** 才进入正常固件。
- `recover_internal_panda()` 会把 panda 强制送进 bootloader，实测 **20 秒以上** 都无法恢复。
- 旧 pandad 在 reset 后没有等待 panda 启动完成，立即查询，看到 panda 还在 bootstub，就判定固件过期并执行刷写。
- 每次刷写耗时约 9 秒。
- `PandaDFU.list()` 在 panda 启动过程中还会阻塞约 7 秒。

以上叠加，导致 panda 上线耗时 41 秒左右。

## 解决方案

### 1. `get_expected_signature()`：保持原样，不做修改

按需求保留原函数逻辑，包括原有的异常处理和返回值行为。固件构建检查相关代码不加入，也不改动。

### 2. pandad 主循环：等待 panda 正常启动后再决定是否刷写

```python
# Handle missing internal panda
if no_internal_panda_count > 0:
  cloudlog.info("No pandas found, resetting internal panda")
  HARDWARE.reset_internal_panda()
  # The internal panda takes a few seconds to boot its app after a reset.
  # Wait for it to come back in normal mode before deciding to flash.
  panda_serials: list[str] = []
  for _ in range(16):
    panda_serials = Panda.list()
    if len(panda_serials) == 1:
      try:
        with Panda(panda_serials[0]) as p:
          if not p.bootstub:
            break
      except Exception:
        pass
    time.sleep(0.5)
```

要点：

- 只执行 reset，不再用 `recover_internal_panda()` 把 panda 送进 bootloader。
- 每 0.5 秒轮询一次，最多等 8 秒，等 panda 回到正常模式（`bootstub=False`）。
- 只有 panda 确实等不到时才检查 DFU 并执行恢复。
- 如果 panda 真是 bootstub 或签名不一致，仍然正常刷写。

## 验证结果

### 设备实测

| 项目 | 修复前 | 修复后 |
| --- | --- | --- |
| pandad wrapper 到真正 pandad | 约 41 秒 | 约 7 秒 |
| panda 状态 | 每次开机 bootstub 并刷写 | 正常固件，无重复刷写 |
| 签名读取 | 保持原样 | 保持原样，不做修改 |

重启后状态：

```text
03:04:45 UTC  manager 启动
03:04:48 UTC  pandad wrapper 启动
03:04:55 UTC  真正 pandad 启动
panda: bootstub=False, version=DEV-844fd084-DEBUG
```

### 重启监控时间线

```text
11:04:07      TCP_DOWN
...
11:04:55.196  TCP_DOWN
11:05:01.223  TCP_UP SSH_OK   <- SSH 第一次真正连上
```

## 涉及文件

设备上修改：

- `/data/openpilot/openpilot/selfdrive/pandad/pandad.py`（当前运行）
- `/data/safe_staging/finalized/openpilot/selfdrive/pandad/pandad.py`（pending overlay 更新副本）

状态：最后一次同步设备端时设备离线，最新版本尚未上传；设备在线后需要重新同步。

本地修改后的代码副本：

- `outputs/pandad_patched.py`

备注：设备 git 仓库中只有 `openpilot/selfdrive/pandad/pandad.py` 被修改，尚未提交。

---

## 仓库内修改记录（2026-08-04）

上述修复（问题 2 的主循环等待逻辑）已应用到本仓库工作副本：

- `openpilot/selfdrive/pandad/pandad.py` — 主循环只执行 `HARDWARE.reset_internal_panda()`，并在 reset 后每 0.5s 轮询（最多 16 次）等待 panda 回到正常模式（`bootstub=False`）再决定是否刷写；只有 panda 确实等不到时才检查 DFU 并恢复。
- `get_expected_signature()` 保持原样（含 `fd27160` 的 scons 自动构建逻辑），未做修改。
- 文档同步：`docs/DP111PRE_DEVICE_FIXES.md` 新增第七节。

工作副本修改尚未提交（`git status` 可见 `selfdrive/pandad/pandad.py` 变更）。设备端仍为离线状态，在线后需重新同步并验证。
