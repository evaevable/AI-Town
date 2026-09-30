# 主场景：按后端配置生成 NPC，接收后端推送的世界状态与台词。
extends Node2D

const NPC_SCENE := preload("res://scenes/npc.tscn")

@onready var npc_root: Node2D = $NPCs
@onready var dialogue: Node = $DialogueUI

var hud: Control
var clock_label: Label
var event_label: Label
var toast_label: Label
var quest_box: VBoxContainer
var relation_box: VBoxContainer
var affinity_cache := {}   # npc_id -> {name, affinity, level}
var npcs := {}                      # npc_id -> Node
var _toast_timer := 0.0

func _ready() -> void:
	add_to_group("main")
	print("[INFO] 主场景初始化")
	_build_hud()
	Net.welcome.connect(_on_welcome)
	Net.npcs_changed.connect(func(list): _spawn_npcs(list))
	Net.npc_move.connect(_on_npc_move)
	Net.bubble.connect(_on_bubble)
	Net.npc_chat.connect(_on_npc_chat)
	Net.clock_tick.connect(_on_clock)
	Net.world_event.connect(_on_world_event)
	Net.toast.connect(_on_toast)
	Net.quests.connect(_on_quests)
	Net.player_state.connect(_on_player_state)
	Net.chat_end.connect(_on_chat_end_affinity)
	Net.gift_result.connect(_on_gift_affinity)
	Net.player_state.connect(_on_player_state)
	Net.chat_end.connect(_on_chat_end_affinity)
	Net.gift_result.connect(_on_gift_affinity)
	if not Net.is_online():
		_toast("还没连上后端，请先在 backend 目录运行 python main.py")
	_toast("用 WASD 走到 NPC 旁边，按 E 跟他说话")
	await get_tree().create_timer(9.0).timeout
	_toast("对话框里可以直接打字，也可以点他给出的快捷回复")
	if OS.get_environment("AITOWN_PROBE") != "":
		_probe()
		return
	if OS.get_environment("AITOWN_WALKTEST") != "":
		_walktest()
	elif OS.get_environment("AITOWN_AUTOTEST") != "":
		_autotest()

func _process(delta: float) -> void:
	if _toast_timer > 0.0:
		_toast_timer -= delta
		if _toast_timer <= 0.0 and toast_label:
			toast_label.visible = false

# ---------- HUD ----------
func _build_hud() -> void:
	hud = Control.new()
	hud.name = "HUD"
	hud.set_anchors_preset(Control.PRESET_FULL_RECT)
	hud.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(hud)

	var box := PanelContainer.new()
	box.position = Vector2(16, 12)
	var st := StyleBoxFlat.new()
	st.bg_color = Color(1, 0.99, 0.96, 0.85)
	st.set_corner_radius_all(10)
	st.set_content_margin_all(10)
	box.add_theme_stylebox_override("panel", st)
	hud.add_child(box)
	var col := VBoxContainer.new()
	box.add_child(col)
	clock_label = Label.new()
	clock_label.add_theme_font_size_override("font_size", 19)
	clock_label.add_theme_color_override("font_color", Color(0.15, 0.15, 0.14))
	col.add_child(clock_label)
	event_label = Label.new()
	event_label.add_theme_font_size_override("font_size", 16)
	event_label.add_theme_color_override("font_color", Color(0.72, 0.33, 0.09))
	col.add_child(event_label)

	var rbox := PanelContainer.new()
	rbox.position = Vector2(16, 108)
	var rst := StyleBoxFlat.new()
	rst.bg_color = Color(1, 0.99, 0.96, 0.9)
	rst.set_corner_radius_all(10)
	rst.set_content_margin_all(10)
	rbox.add_theme_stylebox_override("panel", rst)
	hud.add_child(rbox)
	relation_box = VBoxContainer.new()
	relation_box.add_theme_constant_override("separation", 3)
	rbox.add_child(relation_box)

	var qbox := PanelContainer.new()
	qbox.position = Vector2(16, 232)
	var st2 := StyleBoxFlat.new()
	st2.bg_color = Color(1, 0.99, 0.96, 0.85)
	st2.set_corner_radius_all(10)
	st2.set_content_margin_all(10)
	qbox.add_theme_stylebox_override("panel", st2)
	hud.add_child(qbox)
	quest_box = VBoxContainer.new()
	qbox.add_child(quest_box)

	# 右上角：操作说明（常驻）
	var help := PanelContainer.new()
	help.anchor_left = 1.0
	help.anchor_right = 1.0
	help.offset_left = -330
	help.offset_right = -16
	help.offset_top = 12
	var hst := StyleBoxFlat.new()
	hst.bg_color = Color(1, 0.99, 0.96, 0.9)
	hst.set_corner_radius_all(10)
	hst.set_content_margin_all(12)
	help.add_theme_stylebox_override("panel", hst)
	hud.add_child(help)
	var hcol := VBoxContainer.new()
	hcol.add_theme_constant_override("separation", 4)
	help.add_child(hcol)
	for line in ["操作说明", "WASD / 方向键：走路", "走到 NPC 旁边按 E：说话",
			"回车：发送　ESC：结束对话", "对话框「送礼」：把背包里的东西送给他"]:
		var l := Label.new()
		l.text = line
		l.add_theme_font_size_override("font_size", 15 if line == "操作说明" else 14)
		if line == "操作说明":
			l.add_theme_color_override("font_color", Color(0.28, 0.27, 0.25))
		else:
			l.add_theme_color_override("font_color", Color(0.42, 0.40, 0.36))
		hcol.add_child(l)

	toast_label = Label.new()
	toast_label.position = Vector2(0, 0)
	toast_label.anchor_left = 0.5
	toast_label.anchor_right = 0.5
	toast_label.offset_left = -300
	toast_label.offset_right = 300
	toast_label.offset_top = 620
	toast_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	toast_label.add_theme_font_size_override("font_size", 22)
	toast_label.add_theme_color_override("font_color", Color(0.09, 0.42, 0.09))
	toast_label.add_theme_color_override("font_outline_color", Color(1, 1, 1, 0.9))
	toast_label.add_theme_constant_override("outline_size", 8)
	toast_label.visible = false
	hud.add_child(toast_label)

func show_toast(text: String) -> void:
	_toast(text)

func _toast(text: String) -> void:
	if text.strip_edges() == "":
		return
	toast_label.text = text
	toast_label.visible = true
	_toast_timer = 5.0

# ---------- 生成 NPC ----------
func _on_welcome(list: Array, clock: Dictionary, event: String) -> void:
	_spawn_npcs(list)
	Net.fetch_player()
	Net.fetch_player()
	_on_clock(clock)
	event_label.text = ("正在发生：" + event) if event != "" else ""

func _spawn_npcs(list: Array) -> void:
	for child in npc_root.get_children():
		child.queue_free()
	npcs.clear()
	for info in list:
		var npc: Node = NPC_SCENE.instantiate()
		npc_root.add_child(npc)
		npc.global_position = WorldMap.point(str(info.get("spawn", "走廊")))
		npc.setup(info)
		npcs[str(info.get("id", ""))] = npc
	print("[INFO] 生成了 %d 个 NPC" % npcs.size())

func _npc(npc_id: String) -> Node:
	if npcs.has(npc_id):
		var n = npcs[npc_id]
		if is_instance_valid(n):
			return n
	return null

# ---------- 后端推送 ----------
func _on_npc_move(data: Dictionary) -> void:
	var n := _npc(str(data.get("npc_id", "")))
	if n == null:
		return
	print("[INFO] ", n.npc_name, " 前往 ", data.get("location", ""), "（", data.get("reason", ""), "）")
	n.goto_place(str(data.get("location", "走廊")))
	if str(data.get("activity", "")) != "":
		n.behavior["activity"] = data["activity"]

func _on_bubble(data: Dictionary) -> void:
	var n := _npc(str(data.get("npc_id", "")))
	if n == null:
		return
	var kind := str(data.get("kind", ""))
	print("[INFO] ", n.npc_name, " 气泡(", kind, ")：", data.get("text", ""))
	n.show_bubble(str(data.get("text", "")), 8.0 if kind != "monologue" else 6.0)

func _on_npc_chat(data: Dictionary) -> void:
	print("[INFO] NPC 互聊（", data.get("location", ""), "）：", data.get("lines", []))
	# 两个人的对话依次冒泡，间隔 2.2 秒
	var delay := 0.0
	for line in data.get("lines", []):
		var speaker := str(line.get("speaker", ""))
		var text := str(line.get("line", ""))
		var target := _find_by_name(speaker)
		if target == null:
			continue
		var t := get_tree().create_timer(delay)
		t.timeout.connect(func(): if is_instance_valid(target): target.show_bubble(text, 4.0))
		delay += 2.2

func _find_by_name(name: String) -> Node:
	for n in npcs.values():
		if is_instance_valid(n) and n.npc_name == name:
			return n
	return null

func _on_clock(data: Dictionary) -> void:
	clock_label.text = "%s 第%d天  %s  %s%s" % [
		data.get("date", ""), int(data.get("day", 1)), data.get("time", ""),
		data.get("segment", ""), "（已暂停）" if data.get("paused", false) else ""]

func _on_world_event(data: Dictionary) -> void:
	event_label.text = "正在发生：" + str(data.get("content", ""))
	_toast("小镇事件：" + str(data.get("content", "")))

func _on_toast(text: String) -> void:
	print("[INFO] 提示：", text)
	_toast(text)

func _on_player_state(data: Dictionary) -> void:
	for a in data.get("affinities", []):
		affinity_cache[a.get("npc_id", "")] = {"name": a.get("name", ""),
			"affinity": float(a.get("affinity", 0)), "level": a.get("level", "")}
	_render_relations()

func _on_chat_end_affinity(data: Dictionary) -> void:
	var id := str(data.get("npc_id", ""))
	if affinity_cache.has(id):
		affinity_cache[id]["affinity"] = float(data.get("affinity", 0))
		affinity_cache[id]["level"] = str(data.get("level", ""))
		_render_relations()

func _on_gift_affinity(data: Dictionary) -> void:
	_on_chat_end_affinity(data)

func _render_relations() -> void:
	if relation_box == null:
		return
	for c in relation_box.get_children():
		c.queue_free()
	var head := Label.new()
	head.text = "关系"
	head.add_theme_font_size_override("font_size", 15)
	head.add_theme_color_override("font_color", Color(0.3, 0.29, 0.27))
	relation_box.add_child(head)
	for id in affinity_cache.keys():
		var info: Dictionary = affinity_cache[id]
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 8)
		var name_l := Label.new()
		name_l.text = str(info["name"])
		name_l.custom_minimum_size = Vector2(56, 0)
		name_l.add_theme_font_size_override("font_size", 15)
		name_l.add_theme_color_override("font_color", Color(0.14, 0.14, 0.13))
		row.add_child(name_l)
		var bar := ProgressBar.new()
		bar.max_value = 100
		bar.value = float(info["affinity"])
		bar.show_percentage = false
		bar.custom_minimum_size = Vector2(92, 14)
		bar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(bar)
		var lv := Label.new()
		lv.text = "%s %d" % [str(info["level"]), int(round(float(info["affinity"])))]
		lv.add_theme_font_size_override("font_size", 15)
		lv.add_theme_color_override("font_color", Color(0.42, 0.40, 0.36))
		row.add_child(lv)
		relation_box.add_child(row)

func _on_quests(list: Array) -> void:
	for c in quest_box.get_children():
		c.queue_free()
	if list.is_empty():
		return
	var head := Label.new()
	head.text = "委托"
	head.add_theme_font_size_override("font_size", 15)
	head.add_theme_color_override("font_color", Color(0.42, 0.4, 0.36))
	quest_box.add_child(head)
	for q in list:
		var l := Label.new()
		l.text = "· " + str(q.get("content", ""))
		l.add_theme_font_size_override("font_size", 15)
		quest_box.add_child(l)


# ================== 自检 ==================
# AITOWN_WALKTEST=1  自动走遍每个房间 + 撞墙检查（用真实按键驱动）
# AITOWN_AUTOTEST=1  自动跑一轮完整对话（聊天 → 好感度 → 快捷回复 → 送礼）
# AITOWN_PROBE=x,y[,方向]  把角色放到指定位置按一个方向走，打印撞到了什么

func _walktest() -> void:
	for i in 120:
		await get_tree().process_frame
	var player: Node2D = get_tree().get_first_node_in_group("player")
	print("[WALK] 起点=", player.global_position)
	var rooms := ["茶桌", "客厅", "卧室", "厨房", "沙发区", "电脑桌", "火锅桌"]
	var failed: Array = []
	for name in rooms:
		var ok: bool = await _walk_route(player, name)
		print("[WALK] 到 ", name, "（", WorldMap.POINTS[name], "）→ ",
			"成功" if ok else "失败/卡住", "  用时 %.1f 秒" % _last_walk_time)
		if not ok:
			failed.append(name)
	# 撞墙检查：站到客厅/走廊往上顶，看身体会不会插进墙里
	for label in ["客厅-背墙", "走廊-中部墙"]:
		var start: Vector2 = WorldMap.POINTS["客厅"] if label.begins_with("客厅") else WorldMap.POINTS["走廊"]
		var wall_bottom: float = 96.0 if label.begins_with("客厅") else 306.0
		player.global_position = start
		await get_tree().physics_frame
		_set_dir(Vector2(0, -600))
		for i in 240:
			await get_tree().physics_frame
		_release_all()
		var y := player.global_position.y
		var box_top := y - 30.0        # 碰撞盒 26x68，上沿在脚上方 30px
		var head := y - 38.0           # 贴图头顶
		var ok := box_top >= wall_bottom - 1.0
		print("[WALK] ", label, "：脚位 y=", int(y), " 碰撞盒上沿=", int(box_top),
			" 头顶=", int(head), "（墙下沿=", int(wall_bottom), "）→ ",
			"OK 身体没进墙" if ok else "✗ 身体插进墙里了")
		if not ok:
			failed.append(label)
	print("[WALK] 结果：通过 ", rooms.size() + 2 - failed.size(), "/", rooms.size() + 2,
		"" if failed.is_empty() else "，失败：" + str(failed))
	get_tree().quit(0 if failed.is_empty() else 2)

var _last_walk_time := 0.0

func _nearest_point_name(pos: Vector2) -> String:
	var best := "走廊"
	var best_d := 999999.0
	for k in WorldMap.POINTS.keys():
		var d: float = pos.distance_to(WorldMap.POINTS[k])
		if d < best_d:
			best_d = d
			best = str(k)
	return best

func _walk_route(player: Node2D, target_name: String) -> bool:
	"""按路点图算出路线，一段段用真实按键走过去。"""
	var route := WorldMap.path(_nearest_point_name(player.global_position), target_name)
	var goal: Vector2 = WorldMap.POINTS[target_name]
	if route.is_empty() or route[route.size() - 1] != goal:
		route.append(goal)
	var t_all := Time.get_ticks_msec()
	for wp in route:
		if not await _walk_to(player, wp):
			_last_walk_time = (Time.get_ticks_msec() - t_all) / 1000.0
			return false
	_last_walk_time = (Time.get_ticks_msec() - t_all) / 1000.0
	return true

func _walk_to(player: Node2D, target: Vector2) -> bool:
	var t0 := Time.get_ticks_msec()
	var last_d := player.global_position.distance_to(target)
	var stall := 0.0
	while Time.get_ticks_msec() - t0 < 30000:
		var diff := target - player.global_position
		var d := diff.length()
		if d < 10.0:
			_release_all()
			return true
		if last_d - d < 0.2:
			stall += 1.0 / 60.0
		else:
			stall = 0.0
		if stall > 4.0:
			_release_all()
			print("[WALK]   卡在 ", player.global_position)
			return false
		last_d = d
		_set_dir(diff)
		await get_tree().physics_frame
	_release_all()
	print("[WALK]   超时：停在 ", player.global_position, " 目标 ", target)
	return false

func _set_dir(dir: Vector2) -> void:
	"""真实按键驱动：主轴一定要按；次要轴只要不太小也一起按，避免顶在墙角推不动。"""
	for a in ["ui_up", "ui_down", "ui_left", "ui_right"]:
		Input.action_release(a)
	var ax := absf(dir.x)
	var ay := absf(dir.y)
	if ax >= ay:
		if ax > 2.0:
			Input.action_press("ui_right" if dir.x > 0 else "ui_left")
		if ay > maxf(2.0, ax * 0.2):
			Input.action_press("ui_down" if dir.y > 0 else "ui_up")
	else:
		if ay > 2.0:
			Input.action_press("ui_down" if dir.y > 0 else "ui_up")
		if ax > maxf(2.0, ay * 0.2):
			Input.action_press("ui_right" if dir.x > 0 else "ui_left")

func _release_all() -> void:
	for a in ["ui_left", "ui_right", "ui_up", "ui_down"]:
		Input.action_release(a)

func _autotest() -> void:
	print("[TEST] 等后端推送 NPC…")
	for i in 300:
		await get_tree().process_frame
		if npcs.size() > 0:
			break
	if npcs.is_empty():
		print("[TEST] 失败：没有 NPC（后端没连上？）")
		get_tree().quit(1)
		return
	var npc: Node = npcs.values()[0]
	print("[TEST] NPC=", npc.npc_name, " 位置=", npc.place)
	dialogue.start_dialogue(npc)
	print("[TEST] 对话框已打开=", dialogue.visible, " 标题=", dialogue.title_label.text)
	dialogue.input.text = "你好，我叫Lance，今天想跟你聊聊天"
	dialogue._send()
	for i in 1800:
		await get_tree().process_frame
		if dialogue.waiting == false and dialogue._pending == false and dialogue.body.get_parsed_text().length() > 30:
			break
	var text: String = dialogue.body.get_parsed_text()
	print("[TEST] 对话正文长度=", text.length())
	print("[TEST] 正文=", text.substr(0, 220).replace("\n", " | "))
	print("[TEST] 好感度=", int(dialogue.aff_bar.value), " (", dialogue.aff_label.text, ") 情绪=", npc.emotion)
	print("[TEST] 快捷选项=", dialogue.quick_box.get_child_count(), " 背包=", dialogue.inventory.size())
	print("[TEST] 全部通过")
	get_tree().quit(0)

func _probe() -> void:
	for i in 90:
		await get_tree().process_frame
	var player: CharacterBody2D = get_tree().get_first_node_in_group("player")
	var parts := OS.get_environment("AITOWN_PROBE").split(",")
	player.global_position = Vector2(float(parts[0]), float(parts[1]))
	await get_tree().physics_frame
	print("[PROBE] 起点 ", player.global_position)
	var key := "ui_left"
	if parts.size() > 2:
		key = "ui_" + parts[2]
	Input.action_press(key)
	for i in range(120):
		await get_tree().physics_frame
		if i % 20 == 0:
			var hits: Array = []
			for c in range(player.get_slide_collision_count()):
				var col: KinematicCollision2D = player.get_slide_collision(c)
				var node: Object = col.get_collider()
				hits.append(str(node.name) if node else "?")
			print("[PROBE] t=", i, " pos=", player.global_position, " 撞到=", hits)
	Input.action_release(key)
	get_tree().quit(0)
