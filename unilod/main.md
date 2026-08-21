# 白盒高斯LOD-项目文档

## 一.项目背景

游戏渲染中通常会使用LOD(level of detail)技术来优化性能。LOD指的是同一个物体在不同远近下使用不同精度不同的模型，例如在远处高模Mesh和低模Mesh的视觉效果会很接近，显然后者的开销更小。

游戏资产通常以Mesh的形式制备，但减面后的Mesh在远处的渲染效果往往不佳。3DGS是一种新的场景表示方法，在较远距离下场景拟合的真实感很高，本项目希望利用3DGS这一优势，实现一项“近处Mesh，远处3DGS”的LOD渐变切换的技术。

## 二.工作流程

3DGS原本是一种三维重建的方法，场景的光照阴影细节会被烘焙进高斯球的球谐系数中。因此原始的3DGS是不支持重光照的，这在游戏渲染中没有意义。为了支持重光照，本项目需要从Mesh模型得到其3DGS表示，同时对原模型的几何、材质进行解耦。

本项目自定义了一种3DGS的ply格式（见[本文档](./mesh_to_3dgs_tree_ply_params.md)），原格式中每个高斯球的球谐系数被换成RGB、法线、粗糙度、金属度四种属性（3+3+1+1=8个通道）。

工作流程包含白盒Mesh采样、3DGS优化

### 1.白盒Mesh采样：

从Mesh模型上采样得到3DGS表示，采样对象包括Mesh的几何、albedo（基础色）、normal、roughness和metallic

(1)Mesh数据集制备

推荐从[这个网站](www.cgtrader.com)找素材

很多mesh模型的结构比较复杂，比如包含很多小mesh、有很多种材质属性贴图、同一种属性有很多张贴图（不同位置用不同贴图）、有骨骼和动画等等。为了便于处理建议尽量找简单的，比如只有一两个子mesh、每种属性的贴图不超过三四张的。

以[这个模型](https://www.cgtrader.com/free-3d-models/plant/conifer/american-elm-tree-78c0a9f7-078f-40cf-bbd9-236c991cb019)为例，[演示视频](https://box.nju.edu.cn/f/45c70bb0d6c24bf6a5dc/)

要点:
1.归一化

2.精简纹理贴图，每个纹理槽保留 basecolor、normal两张图即可，

3.导出为glb，导出时不要勾选y向上。正常情况下重新导回Blender后会发生旋转


(2)Mesh采样

上一步得到合格的glb文件以后，需要按照不同的resolution对白盒Mesh采样，得到不同LOD层次的3DGS ply文件。

采样方法上，前期尝试过不同的采样策略，对比之后目前的方案是**Mesh2splat**这个工具的改造版，改造版代码暂时在[这个仓库](https://github.com/mustard501/MeshSample)

使用方法：

<img src="4.jpg" width="200">

左上角模式选择里，选为"BRDF";

右侧"max resolution", 默认为1024, 暂时可以不动;

右侧"conversion resolution", 表示的是当前采样的resolution, 值越大表示采样率越高, 高斯模型越精细

导入模型后点击"convert"按钮, 会将mesh转为3DGS; 再点击"save"按钮，会保存为一份ply文件和一份csv文件。目前先按512~16的resolution采样6个层次，按"level_0x"的格式命名(16对应level_01, 512对应level_06)

csv文件也保存在同一目录下，后续建lod树会用到；采样导出的ply文件已经是自定义的格式，可以使用根目录下的``convert.py``脚本，将指定目录下的自定义格式ply的四种属性值分别写球谐并导出

<img src="5.jpg" width="200">

可以用supersplat等工具打开每种属性各自的球谐ply来检查颜色、方位是否正常。理论上``albedo_srgb````metallic````roughness``目录下的ply打开后颜色和GT图是完全一致的


### 2.3DGS优化：

采样得到的3DGS效果较差，下一步要用Mesh模型监督训练。但与多视角图重建不同的地方在于，Mesh的几何和四种属性都要参与监督，所以需要使用本项目定制的3DGS训练管线

(1)GT数据制备：

Blender nerf 插件导出basecolor、normal、roughness、metallic四种属性map的GT图，[演示视频](https://box.nju.edu.cn/f/78aa51cc0a21474da05f/)

要点：

1.basecolor的色彩空间用“标准”，另外三种都是“Raw”

2.normal map注意先转到[0,1]再连接材质输出，normal纹理图的节点设为“非色彩”

3.normal图用exr格式，exr文件可以用[这个程序](./scripts/tev.exe)查看

4.插件参数的帧数和相机轨迹帧数保持一致

5.照片分辨率800x800,胶片里把背景设为透明

(2)训练：

（代码还在整理）

### 3.LOD生成

上一步得到训练后的各LOD层级的ply文件，再对各层级建立一个LOD层次树，导出一个树结构文件。后续的混合LOD渲染会基于层次树进行。

（代码还在整理）

### 4.Mesh-3DGS LOD 混合渲染

这部分通过UE插件实现，插件开发由汪科负责