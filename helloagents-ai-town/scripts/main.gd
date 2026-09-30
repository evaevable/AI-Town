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
	if not Net.is_online():
		_toast("还没连上后端，请先在 backend 目录运行 python main.py")
	_toast("用 WASD 走到 NPC 旁边，按 E 跟他说话")
	await get_tree().create_timer(9.0).timeout
	_toast("对话框里可以直接打字，也可以点他给出的快捷回复")
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
	col.add_child(clock_label)
	event_label = Label.new()
	event_label.add_theme_font_size_override("font_size", 16)
	event_label.add_theme_color_override("font_color", Color(0.72, 0.33, 0.09))
	col.add_child(event_label)

	var qbox := PanelContainer.new()
	qbox.position = Vector2(16, 108)
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


# ---------- 自检（AITOWN_AUTOTEST=1 时自动跑一遍对话，方便无界面验证） ----------
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
	print("[TEST] NPC=", npc.npc_name, " 位置=", npc.place, " 贴图=", npc.get_meta("sprite", "-"))
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
	print("[TEST] 对话正文=", text.substr(0, 260).replace("\n", " | "))
	print("[TEST] 好感度=", int(dialogue.aff_bar.value), " (", dialogue.aff_label.text, ") 情绪=", npc.emotion)
	print("[TEST] 快捷选项按钮=", dialogue.quick_box.get_child_count(), " 背包=", dialogue.inventory.size())
	var before: int = dialogue.body.get_parsed_text().length()
	Net.gift(npc.npc_id, "蛋糕")
	for i in 1200:
		await get_tree().process_frame
		if dialogue._pending == false and dialogue.body.get_parsed_text().length() > before:
			break
	print("[TEST] 送礼后正文尾=", dialogue.body.get_parsed_text().right(60).replace("\n", " | "))
	print("[TEST] 全部通过")
	get_tree().quit(0)


# ---------- 走路自检（AITOWN_WALKTEST=1）：自动走到每个房间，检查会不会卡住 ----------
func _walktest() -> void:
	for i in 120:
		await get_tree().process_frame
	var player: Node2D = get_tree().get_first_node_in_group("player")
	var start := player.global_position
	print("[WALK] 起点=", start)
	var targets := {"茶桌": "茶桌", "客厅": "客厅", "卧室": "卧室", "厨房": "厨房",
		"沙发区": "沙发区", "电脑桌": "电脑桌", "火锅桌": "火锅桌"}
	var failed: Array = []
	for name in targets.keys():
		var ok: bool = await _walk_route(player, name)
		print("[WALK] 到 ", name, "（", WorldMap.POINTS[name], "）→ ", "成功" if ok else "失败/卡住",
			"  用时 %.1f 秒" % _last_walk_time)
		if not ok:
			failed.append(name)
	# 边界检查：站到客厅一直往上顶，看会不会钻到背墙/屋子外面去
	var before: Vector2 = player.global_position
	player.global_position = WorldMap.POINTS["客厅"]
	await get_tree().physics_frame
	_set_dir(Vector2(0, -1))
	for i in 240:
		await get_tree().physics_frame
	_release_all()
	var top_y := player.global_position.y
	print("[WALK] 从客厅往上顶到底 y=", int(top_y), "（脚部碰撞盒上沿=", int(top_y + 18), "，墙沿 y=96）",
		"  OK 没出屋子" if top_y > 75 else "  ✗ 跑出屋子了")
	player.global_position = before
	print("[WALK] 结果：成功 ", targets.size() - failed.size(), "/", targets.size(),
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
	"""先按路点图算出路线，再一段段走过去（和 NPC 用的是同一张图）。"""
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
	while Time.get_ticks_msec() - t0 < 15000:
		var d := player.global_position.distance_to(target)
		if d < 22.0:
			_release_all()
			_last_walk_time = (Time.get_ticks_msec() - t0) / 1000.0
			return true
		if last_d - d < 0.2:
			stall += 1.0 / 60.0
		else:
			stall = 0.0
		if stall > 3.0:   # 三秒没进展就算卡住
			_release_all()
			_last_walk_time = (Time.get_ticks_msec() - t0) / 1000.0
			print("[WALK]   卡在 ", player.global_position)
			return false
		last_d = d
		var dir := (target - player.global_position).normalized()
		_set_dir(dir)
		await get_tree().physics_frame
	_release_all()
	_last_walk_time = (Time.get_ticks_msec() - t0) / 1000.0
	return false

func _set_dir(dir: Vector2) -> void:
	if dir.x > 0.25:
		if not Input.is_action_pressed("ui_right"):
			Input.action_press("ui_right")
	else:
		Input.action_release("ui_right")
	if dir.x < -0.25:
		if not Input.is_action_pressed("ui_left"):
			Input.action_press("ui_left")
	else:
		Input.action_release("ui_left")
	if dir.y > 0.25:
		if not Input.is_action_pressed("ui_down"):
			Input.action_press("ui_down")
	else:
		Input.action_release("ui_down")
	if dir.y < -0.25:
		if not Input.is_action_pressed("ui_up"):
			Input.action_press("ui_up")
	else:
		Input.action_release("ui_up")

func _release_all() -> void:
	for a in ["ui_left", "ui_right", "ui_up", "ui_down"]:
		Input.action_release(a)
