# 场景地图：地点名 → 像素坐标 + 路点图（BFS 找路）。
# 后端只给地点名，具体怎么走由这里决定。想调整位置改这张表即可。
extends RefCounted
class_name WorldMap

const POINTS := {
	"卧室": Vector2(1180, 268),
	"客厅": Vector2(480, 195),
	"厨房": Vector2(215, 205),
	"沙发区": Vector2(965, 238),
	"电脑桌": Vector2(1000, 470),
	"茶桌": Vector2(560, 480),
	"火锅桌": Vector2(280, 420),
	"走廊": Vector2(650, 480),
	"_top_mid": Vector2(700, 180),
	"_pass": Vector2(780, 268),
	"_top_right": Vector2(1100, 250),
	"_gap": Vector2(1100, 370),
	"_br": Vector2(1150, 480),
}

const EDGES := [
	["卧室", "沙发区"],
	["厨房", "客厅"],
	["客厅", "_top_mid"],
	["_top_mid", "_pass"],
	["_top_mid", "沙发区"],
	["沙发区", "_top_right"],
	["_top_right", "_gap"],
	["_gap", "_br"],
	["_br", "电脑桌"],
	["电脑桌", "走廊"],
	["_pass", "走廊"],
	["走廊", "茶桌"],
	["茶桌", "火锅桌"],
]

# 只暴露给后端/UI 的真实地点（含下划线的是内部路点）
static func locations() -> Array:
	var out: Array = []
	for k in POINTS.keys():
		if not str(k).begins_with("_"):
			out.append(k)
	return out

static func point(place: String) -> Vector2:
	return POINTS.get(place, POINTS["走廊"])

static func path(from_place: String, to_place: String) -> Array:
	"""返回 [Vector2, ...]，从当前位置走向目标地点（BFS 最短路）。"""
	var start := from_place if POINTS.has(from_place) else "_walk"
	var goal := to_place if POINTS.has(to_place) else "_walk"
	if start == goal:
		return []
	var adj := {}
	for e in EDGES:
		adj.get_or_add(e[0], []).append(e[1])
		adj.get_or_add(e[1], []).append(e[0])
	var queue: Array = [start]
	var prev := {start: ""}
	while queue.size() > 0:
		var cur: String = queue.pop_front()
		if cur == goal:
			break
		for nxt in adj.get(cur, []):
			if not prev.has(nxt):
				prev[nxt] = cur
				queue.append(nxt)
	if not prev.has(goal):
		return [point(to_place)]
	var chain: Array = []
	var node := goal
	while node != "":
		chain.push_front(point(node))
		node = prev[node]
	chain.pop_front()  # 去掉起点自身
	return chain
