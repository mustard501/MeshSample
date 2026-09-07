# meshsample

**meshsample** 将 **glTF Binary（`.glb`）** 网格在 GPU 上快速采样为 **3D Gaussian Splatting（3DGS）** 点云，并导出为 PLY。项目当前定位为 **mesh → 高斯采样与导出工具**，不再包含内置 GS 渲染预览。

<div align="center">
    <img src="./res/conversion.gif" width="850px">
</div>

---

## 项目概览

meshsample 直接利用网格的几何、UV 与 glTF PBR 材质（base color、metallic-roughness、normal），在正交 UV 空间中为每个 rasterized fragment 生成一个高斯，典型耗时在 **毫秒级**。

**典型工作流：**

1. 在 GUI 中加载 `.glb`
2. 调整采样分辨率与 Gaussian Scale
3. 导出 PLY（推荐 **BRDF 格式** 用于自定义 PBR 3DGS 管线）
4. （可选）用 `convert.py` 将 BRDF PLY 拆成 per-attribute 的 SH PLY，便于调试

BRDF PLY 字段定义见 [`docs/mesh_to_3dgs_tree_ply_params.md`](docs/mesh_to_3dgs_tree_ply_params.md)。

---

## 功能

| 能力 | 说明 |
|------|------|
| 输入 | `.glb`（mesh → 采样）；`.ply`（仅加载已有 3DGS，可 re-export） |
| 材质 | glTF 2.0 Metallic-Roughness：`baseColor`、MR 贴图、normal 贴图 |
| 采样密度 | `Conversion resolution`（16…4096，2 的幂）与 `Gaussian Scale` 滑块 |
| 批量 | UI 内 Batch 面板，对文件夹内多个 GLB 依次采样并导出 |
| Shader 热重载 | 修改 `src/shaders/conversion/` 下 shader 后自动检测并重编译 |

---

## 环境要求

- **Windows**（当前主要支持平台）
- **CMake ≥ 3.21**（Visual Studio 2022 生成器）
- **Visual Studio 2019 / 2022**，含 **「使用 C++ 的桌面开发」** 工作负载
- 支持 **OpenGL 4.6** 的 GPU 与驱动

第三方库已 vendored 于 `thirdParty/`（GLFW、GLEW、GLM、ImGui、tinygltf、xatlas 等），无需单独安装。

---

## 构建

在项目根目录打开 **cmd** 或 **PowerShell**：

```bat
run_build_release.bat
```

或手动：

```bat
mkdir build
cd build
cmake ..
cmake --build . --config Release
cd ..
```

可执行文件输出路径：

```
bin/Release/meshsample.exe
```

Debug 构建可使用 `run_build_debug.bat`，输出在 `bin/Debug/`。

---

## 使用方法

### 1. 启动

运行 `bin/Release/meshsample.exe`，打开 ImGui 界面。

### 2. 单次转换

1. **File Selector → Select file to load**，选择 `.glb`
2. 点击 **Convert Mesh to 3DGS**（加载 mesh 并触发 GPU 采样）
3. **Properties** 中调整：
   - **Gaussian Scale**：高斯在切平面上的尺度（影响导出 `scale_*`）
   - **Max resolution (cap)** / **Conversion resolution (2^n)**：采样分辨率，越高高斯越多、越慢
4. **Select output folder** 选择输出目录，填写文件名
5. 在格式下拉框选择导出类型，点击 **Save splat**

### 3. 导出格式

| 选项 | 说明 |
|------|------|
| PLY Standard Format | 经典 3DGS PLY；同时写出 companion `.csv`（像素/三角面索引，调试用） |
| PLY PBR | 带 metallic/roughness 的 PBR PLY |
| PLY Compressed PBR | 压缩 PBR 格式 |
| **PLY BRDF (base sRGB, N, MR)** | 自定义 BRDF 属性 PLY：`base_r/g/b`（sRGB）、法线、metallic、roughness、opacity、scale、rotation |

日常 BRDF 3DGS 管线请使用最后一项（format **3**）。

### 4. 批量处理

在 **Batch** 面板添加输入目录与输出规则，点击 **Run batch**。应用会在主循环中依次：加载 GLB → 采样 → 导出 PLY。

### 5. 可选：`convert.py`（调试）

将目录下 BRDF PLY 拆分为 albedo / normal / roughness / metallic 等 SH PLY，便于在外部查看器检查各通道：

```bat
pip install numpy plyfile
python convert.py
```

默认处理当前目录下的 `1/` 文件夹；修改脚本末尾 `target_directory` 即可。

---

## 目录结构（简要）

```
src/
  conversion/          # 采样 meta（GaussianPixelTable）与 CSV 导出
  renderer/renderPasses/ConversionPass.cpp
  shaders/conversion/  # 转换用 GLSL
  utils/SceneManager.cpp   # GLB 解析、纹理上传、PLY 导出
  imGuiUi/             # 操作界面
docs/
  mesh_to_3dgs_tree_ply_params.md   # BRDF PLY schema
convert.py             # BRDF PLY → SH PLY（调试，非核心）
```

---

## 限制

- 仅支持 **三角面** glTF primitive；非三角形会被跳过
- 贴图来自 **GLB 内嵌 image**；外部 URI 贴图路径未完整支持
- 体积类内容（毛发、 foliage 等）不在设计目标内
- 无内置 3DGS 实时预览窗口（界面以参数与导出为主）

---

