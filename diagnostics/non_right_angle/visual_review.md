# 非直角模型外形观察

观察日期：2026-10-27。方法为实际打开生成的离线 `model.html` 查看器；本记录仅为可见外形观察，不代表用户认可、历史工法确认、加工验收或承载验算。

两案来源为本轮生成结果，主代理提供的生成记录：源码 SHA-256 `ef5340fed7c096b557a9258a71b94c618365dc62a33b29de8c8fdef0a1daa33d`。查看器中均开启阴影与三角面网格，木望板透明度设为 0%（不透明）；采用轴测、俯视、侧视、戗轴侧视，另以分组隐藏检查上下层板遮挡下的构件。截图为实际页面状态；截图目录：`C:\Users\24408\.codex\visualizations\2026\10\27\01a17bfb-bd6c-70f0-a665-a5c8c9f76568\angle-visual\`。

## 60°（建筑内角）

模型 `outputs/angle_60_B400_run06/model.html`。生成记录给出的模型规模为 90 构件、每侧 8 模板（16 根立脚飞椽）、212074 三角面。

在整体轴测和俯视中，两翼按较锐内角展开，扇区朝角部收束；老戗、嫩戗与支承位于两翼交界附近。板材不透明时，连续卷戗板的两翼表面和边界普通椽到内部扇椽的分界可辨，未见明确断缝、缺片或明显姿态突变。隐藏上下层板后，飞椽沿两翼呈扇形分布，交界处很密集，但没有看到可确定的异常细条或突然中断。

截图：

- 轴测：[60_axis.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/60_axis.png)
- 俯视：[60_top.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/60_top.png)
- 侧视：[60_side.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/60_side.png)
- 戗轴侧视：[60_qiang_axis.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/60_qiang_axis.png)
- 隐藏上下层板后的俯视：[60_boards_hidden_top.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/60_boards_hidden_top.png)
- 隐藏上下层板后的轴测：[60_boards_hidden_axis.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/60_boards_hidden_axis.png)

## 120°（建筑内角）

模型 `outputs/angle_120_B400_run01/model.html`。生成记录给出的模型规模为 54 构件、每侧 2 模板（4 根立脚飞椽）、29122 三角面。

整体俯视可见两翼按较钝内角展开；两侧构造随角平分面布置，没有观察到一翼翻转或错向。轴测、侧视及戗轴侧视中，老戗、嫩戗和支承仍位于翼间过渡区域；卷戗板外缘与普通边界椽、扇区椽衔接处未见明确断缝、缺片或明显姿态突变。隐藏上下层板并旋转视角后，交界附近构件较密且相互遮挡，仍未见能确定为异常裁切的细条或断开。

截图：

- 轴测：[120_axis.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/120_axis.png)
- 俯视：[120_top.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/120_top.png)
- 侧视：[120_side.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/120_side.png)
- 戗轴侧视：[120_qiang_axis.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/120_qiang_axis.png)
- 隐藏上下层板后的俯视：[120_boards_hidden_top.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/120_boards_hidden_top.png)
- 隐藏上下层板并局部旋转后的戗轴视角：[120_joint_rotated.png](C:/Users/24408/.codex/visualizations/2026/10/27/01a17bfb-bd6c-70f0-a665-a5c8c9f76568/angle-visual/120_joint_rotated.png)

## 可见结论与覆盖范围

本次指定的非直角外形部位未发现可从这些视图确认的明显几何缺陷。120°翼间交界的槽内接触和局部层叠受遮挡，不能仅凭截图逐一确认；60°同类局部也受构件密集和视线遮挡限制。观察没有检查 1 cm 以下穿插、实体有效性、相交/碰撞、加工可行性或承载表现。
