import bpy
import mathutils

def clear_anim_and_normalize():
    # 确保在物体模式
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    selected_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    
    if not selected_objs:
        print("未选中任何网格对象！")
        return

    print("--- 开始清理动画与约束 ---")
    
    for obj in selected_objs:
        # 1. 彻底斩断并清理动画数据（K帧、驱动器、NLA）
        if obj.animation_data:
            obj.animation_data_clear()
            
        # 2. 清理可能暗中作祟的物体约束（比如 复制位置、限定缩放 等约束）
        obj.constraints.clear()
        
        # 3. 清理父级（防止父级带有动画或缩放）
        # 顺便将物体当前的姿态完全“断后”独立出来
        bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')

    # 4. 第一次强制应用所有基础变换，清空历史包袱
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 5. 精确计算所有对象合并后的全局边界框
    min_coords = mathutils.Vector((float('inf'), float('inf'), float('inf')))
    max_coords = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))

    for obj in selected_objs:
        for vertex in obj.data.vertices:
            v_world = obj.matrix_world @ vertex.co
            for i in range(3):
                min_coords[i] = min(min_coords[i], v_world[i])
                max_coords[i] = max(max_coords[i], v_world[i])

    center = (min_coords + max_coords) / 2
    size = max_coords - min_coords
    max_dim = max(size)

    if max_dim == 0:
        print("模型没有体积，无法归一化。")
        return

    # 最长轴目标跨度为 2（即 -1 到 1）
    scale_factor = 2.0 / max_dim

    # 6. 在物体级别执行平移和缩放
    for obj in selected_objs:
        obj.location = (obj.location - center) * scale_factor
        obj.scale *= scale_factor

    # 7. 第二次强制应用变换！把 0,0,0 和 1,1,1 彻底写入底层网格
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 8. 重置 3D 游标和物体原点到绝对中心
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
    
    print("动画已剔除，模型已安全归一化！请重新保存测试。")

# 执行
clear_anim_and_normalize()