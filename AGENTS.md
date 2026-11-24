# GlassesTest · Agent 持久记忆

> GitHub：`k2736680-kerr/GlassesTest`（`~/AutoTestTools/Projects/GlassesTest`）

## 是什么

Windows 桌面工具：眼镜端 **拍照 / 录音 / 录像 → 导入 → 报告**。测试员只拿 `GlassesTest.exe`（见 `分发说明.txt`）。

## 关键目录

| 路径 | 作用 |
|------|------|
| `desktop/` + `desktop_entry.py` | GUI 与入口 |
| `glasstest/` | 导入、分辨率、复杂流程核心 |
| `selectors.json`、`device_registry.json`、`task_config.json` | 打进 exe 的配置 |
| `打包桌面exe.ps1` | 产出 `_dist/GlassesTest.exe` |
| `准备便携运行时.ps1` | 从本机 Tools 刷新 adb/ffprobe |
| `tools/tests/` | 单元测试 |

## 怎么跑（维护者）

**源码调试（Windows）：**

```bat
setup_env.bat
.venv\Scripts\python.exe -m desktop
```

**打包：**

```powershell
powershell -ExecutionPolicy Bypass -File .\打包桌面exe.ps1
```

输出数据目录：exe 旁 `GlassesTest_data/output/`；源码默认 `D:\GlassesTest_data\output\` 或 env **`GLASSES_DATA_DIR`**。

## 约定

- 改 `selectors.json` 等配置后**必须重新打包** exe。
- `glasstest/run_complex_flow.py` 为长链路入口；报告 HTML 与校验逻辑改动后跑 `tools/tests/`。
- 打包内置 adb 仅 `runtime/platform-tools/` 精简三文件。

## 勿动

- 不要随便升级打包进去的 adb/ffprobe 版本而不跑全量分辨率/导入回归。
- 测试员文档只用 `分发说明.txt`，复杂维护说明在 `交接与使用说明.md`。

## Mac 注意

- **主业是 Windows exe**；Mac 可读代码、跑部分 `tools/tests`（无 GUI 全链路）。
- 迁移若要做跨平台，需替换 PyInstaller 脚本与 adb 路径；当前无 Mac 打包脚本。
- 规格：`图像视频标准输出规格.md`

## 先读

- `交接与使用说明.md`、`分发说明.txt`