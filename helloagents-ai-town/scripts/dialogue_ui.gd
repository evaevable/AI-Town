# 对话界面：流式打字效果、快捷回复、送礼、好感度条、情绪与心情。
# 布局（1280x720 窗口）：打开时整屏压暗，底部居中放 1200x430 的大面板
#   [名字 26px + 心情]                      [好感度条 260x18 + 等级]
#   [正文 22px 可滚动，至少 210px 高，浅底圆角]
#   [快捷回复按钮 17px]
#   [输入框 20px/46 高] [发送] [送礼] [离开]
extends CanvasLayer

const FONT_TITLE := 26
const FONT_TEXT := 22
const FONT_SMALL := 17
const FONT_INPUT := 20
const INK := Color(0.13, 0.13, 0.12)      # 浅底上的正文色
const MUTED := Color(0.42, 0.40, 0.36)

var current: Node = null
var inventory: Array = []

var backdrop: ColorRect
var panel: PanelContainer
var title_label: Label
var aff_bar: ProgressBar
var aff_label: Label
var mood_label: Label
var body: RichTextLabel
var quick_box: HBoxContainer
var input: LineEdit
var send_btn: Button
var gift_btn: Button
var close_btn: Button
var gift_menu: PopupMenu
var waiting := false
var _pending := false   # 已发出请求、正在等这一轮回复（防止并发串台）

func _ready() -> void:
	add_to_group("dialogue_system")
	visible = false
	_build_ui()
	Net.chat_start.connect(_on_chat_start)
	Net.chat_delta.connect(_on_chat_delta)
	Net.chat_replace.connect(_on_chat_replace)
	Net.chat_end.connect(_on_chat_end)
	Net.gift_result.connect(_on_gift_result)
	Net.inventory.connect(_on_inventory)
	Net.toast.connect(_on_toast)

func _mk_button(text: String, size: int = FONT_SMALL, h: int = 44) -> Button:
	var b := Button.new()
	b.text = text
	b.add_theme_font_size_override("font_size", size)
	for state in ["font_color", "font_hover_color", "font_pressed_color", "font_focus_color"]:
		b.add_theme_color_override(state, INK)
	b.add_theme_color_override("font_disabled_color", Color(0.62, 0.6, 0.57))
	var st := StyleBoxFlat.new()
	st.bg_color = Color(0.98, 0.975, 0.96)
	st.border_color = Color(0.72, 0.70, 0.65)
	st.set_border_width_all(1)
	st.set_corner_radius_all(8)
	st.set_content_margin_all(8)
	var st_hover: StyleBoxFlat = st.duplicate()
	st_hover.bg_color = Color(0.93, 0.95, 0.98)
	b.add_theme_stylebox_override("normal", st)
	b.add_theme_stylebox_override("hover", st_hover)
	b.add_theme_stylebox_override("pressed", st_hover)
	b.add_theme_stylebox_override("disabled", st)
	b.custom_minimum_size = Vector2(0, h)
	return b

func _build_ui() -> void:
	backdrop = ColorRect.new()
	backdrop.name = "Backdrop"
	backdrop.color = Color(0.05, 0.05, 0.07, 0.45)
	backdrop.set_anchors_preset(Control.PRESET_FULL_RECT)
	backdrop.mouse_filter = Control.MOUSE_FILTER_STOP
	add_child(backdrop)

	panel = PanelContainer.new()
	panel.name = "Panel"
	panel.anchor_left = 0.5
	panel.anchor_right = 0.5
	panel.anchor_top = 1.0
	panel.anchor_bottom = 1.0
	panel.offset_left = -600
	panel.offset_right = 600
	panel.offset_top = -450
	panel.offset_bottom = -20
	var style := StyleBoxFlat.new()
	style.bg_color = Color(1, 0.995, 0.98, 0.98)
	style.border_color = Color(0.72, 0.70, 0.65)
	style.set_border_width_all(2)
	style.set_corner_radius_all(16)
	style.set_content_margin_all(20)
	panel.add_theme_stylebox_override("panel", style)
	add_child(panel)

	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 12)
	panel.add_child(col)

	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 14)
	col.add_child(head)
	title_label = Label.new()
	title_label.add_theme_font_size_override("font_size", FONT_TITLE)
	title_label.add_theme_color_override("font_color", INK)
	head.add_child(title_label)
	mood_label = Label.new()
	mood_label.add_theme_font_size_override("font_size", FONT_SMALL)
	mood_label.add_theme_color_override("font_color", MUTED)
	mood_label.vertical_alignment = VERTICAL_ALIGNMENT_BOTTOM
	head.add_child(mood_label)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(spacer)
	var aff_title := Label.new()
	aff_title.text = "好感度"
	aff_title.add_theme_font_size_override("font_size", FONT_SMALL)
	aff_title.add_theme_color_override("font_color", MUTED)
	aff_title.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	head.add_child(aff_title)
	aff_bar = ProgressBar.new()
	aff_bar.custom_minimum_size = Vector2(300, 22)
	aff_bar.max_value = 100
	aff_bar.show_percentage = false
	aff_bar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(aff_bar)
	aff_label = Label.new()
	aff_label.add_theme_font_size_override("font_size", FONT_SMALL)
	aff_label.add_theme_color_override("font_color", INK)
	aff_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	head.add_child(aff_label)

	var body_panel := PanelContainer.new()
	var bst := StyleBoxFlat.new()
	bst.bg_color = Color(0.965, 0.958, 0.94, 1.0)
	bst.set_corner_radius_all(12)
	bst.set_content_margin_all(14)
	body_panel.add_theme_stylebox_override("panel", bst)
	body_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	col.add_child(body_panel)
	body = RichTextLabel.new()
	body.bbcode_enabled = true
	body.scroll_following = true
	body.add_theme_color_override("default_color", INK)
	body.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0))
	body.add_theme_font_size_override("normal_font_size", FONT_TEXT)
	body.add_theme_font_size_override("bold_font_size", FONT_TEXT)
	body.add_theme_constant_override("line_separation", 6)
	body.custom_minimum_size = Vector2(0, 210)
	body_panel.add_child(body)

	quick_box = HBoxContainer.new()
	quick_box.add_theme_constant_override("separation", 10)
	col.add_child(quick_box)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	col.add_child(row)
	input = LineEdit.new()
	input.placeholder_text = "说点什么…（回车发送）"
	input.add_theme_font_size_override("font_size", FONT_INPUT)
	input.add_theme_color_override("font_color", INK)
	input.add_theme_color_override("font_placeholder_color", Color(0.55, 0.53, 0.5))
	input.custom_minimum_size = Vector2(0, 46)
	input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(input)
	send_btn = _mk_button("发送", FONT_INPUT, 46)
	send_btn.custom_minimum_size = Vector2(96, 46)
	row.add_child(send_btn)
	gift_btn = _mk_button("送礼", FONT_INPUT, 46)
	gift_btn.custom_minimum_size = Vector2(96, 46)
	row.add_child(gift_btn)
	close_btn = _mk_button("离开 (ESC)", FONT_INPUT, 46)
	close_btn.custom_minimum_size = Vector2(150, 46)
	row.add_child(close_btn)

	gift_menu = PopupMenu.new()
	gift_menu.add_theme_font_size_override("font_size", FONT_SMALL)
	add_child(gift_menu)

	send_btn.pressed.connect(_send)
	input.text_submitted.connect(func(_t): _send())
	gift_btn.pressed.connect(_open_gift_menu)
	gift_menu.id_pressed.connect(_on_gift_id)
	close_btn.pressed.connect(close_dialogue)

func _input(event: InputEvent) -> void:
	if not visible:
		return
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_ESCAPE:
			close_dialogue()
			get_viewport().set_input_as_handled()
		elif event.keycode in [KEY_E, KEY_SPACE, KEY_W, KEY_A, KEY_S, KEY_D]:
			if not input.has_focus() and event.keycode != KEY_SPACE:
				get_viewport().set_input_as_handled()

func start_dialogue(npc) -> void:
	if typeof(npc) == TYPE_STRING:
		npc = _find_npc(str(npc))
	if npc == null:
		return
	current = npc
	if npc.has_method("set_interacting"):
		npc.set_interacting(true)
	title_label.text = "%s · %s" % [npc.npc_name, npc.npc_title]
	mood_label.text = "心情 " + str(npc.behavior.get("mood", ""))
	body.clear()
	body.append_text("[color=#6b6a66]—— 和 %s 的对话（记忆会一直留着，可以随时回来接着聊）——[/color]\n" % npc.npc_name)
	_clear_quick()
	visible = true
	input.grab_focus()
	_set_waiting(false)
	var player := get_tree().get_first_node_in_group("player")
	if player and player.has_method("set_interacting"):
		player.set_interacting(true)

func close_dialogue() -> void:
	if current and current.has_method("set_interacting"):
		current.set_interacting(false)
	current = null
	visible = false
	var player := get_tree().get_first_node_in_group("player")
	if player and player.has_method("set_interacting"):
		player.set_interacting(false)

func open_with(npc) -> void:
	start_dialogue(npc)

func _find_npc(npc_name: String) -> Node:
	for n in get_tree().get_nodes_in_group("npcs"):
		if n.npc_name == npc_name:
			return n
	return null

func _send() -> void:
	if current == null or waiting:
		return
	var text := input.text.strip_edges()
	if text.is_empty():
		return
	input.text = ""
	body.append_text("\n[color=#1d6fa5][b]你[/b][/color]  " + text + "\n")
	body.append_text("[color=#8a8780]%s 在想…[/color]" % current.npc_name)
	_pending = true
	_set_waiting(true)
	Net.say(current.npc_id, text)

func _set_waiting(v: bool) -> void:
	waiting = v
	send_btn.disabled = v
	input.editable = not v

func _clear_quick() -> void:
	for c in quick_box.get_children():
		c.queue_free()

func _on_chat_start(data: Dictionary) -> void:
	if current == null or not _pending or data.get("npc_id", "") != current.npc_id:
		return
	var plain := body.get_parsed_text()
	if plain.ends_with("在想…"):
		body.clear()
		var lines := plain.split("\n")
		for i in range(lines.size() - 1):
			body.append_text(lines[i] + "\n")
	body.append_text("[color=#b0781a][b]%s[/b][/color]  " % current.npc_name)
	_clear_quick()

func _on_chat_delta(data: Dictionary) -> void:
	if current == null or not _pending or data.get("npc_id", "") != current.npc_id:
		return
	body.append_text(str(data.get("text", "")))

func _on_chat_replace(data: Dictionary) -> void:
	if current == null or not _pending or data.get("npc_id", "") != current.npc_id:
		return
	var plain := body.get_parsed_text()
	var idx := plain.rfind(current.npc_name)
	if idx >= 0:
		body.clear()
		body.append_text(plain.substr(0, idx))
	body.append_text("[color=#b0781a][b]%s[/b][/color]  %s" % [current.npc_name, str(data.get("text", ""))])

func _on_chat_end(data: Dictionary) -> void:
	if current == null or not _pending or data.get("npc_id", "") != current.npc_id:
		return
	_pending = false
	body.append_text("\n")
	aff_bar.value = float(data.get("affinity", 0))
	var change := float(data.get("affinity_change", 0))
	_set_affinity_text(data.get("level", ""), aff_bar.value, change)
	_append_change_note(change, data.get("level", ""), int(aff_bar.value), data.get("reason", ""))
	current.set_emotion(str(data.get("emotion", "")))
	for opt in data.get("reply_options", []):
		var b := _mk_button(str(opt), FONT_SMALL, 40)
		b.pressed.connect(func():
			input.text = b.text
			_send())
		quick_box.add_child(b)
	_set_waiting(false)
	input.grab_focus()

func _open_gift_menu() -> void:
	gift_menu.clear()
	if inventory.is_empty():
		gift_menu.add_item("背包是空的", 0)
		gift_menu.set_item_disabled(0, true)
	for i in inventory.size():
		gift_menu.add_item("%s ×%d" % [inventory[i].item, int(inventory[i].count)], i)
	gift_menu.position = Vector2i(get_viewport().get_mouse_position()) + Vector2i(10, 10)
	gift_menu.popup()

func _on_gift_id(id: int) -> void:
	if current == null or id < 0 or id >= inventory.size():
		return
	var item := str(inventory[id].item)
	body.append_text("\n[color=#6b6a66]你送出了 %s…[/color]\n" % item)
	_pending = true
	_set_waiting(true)
	Net.gift(current.npc_id, item)

func _on_gift_result(data: Dictionary) -> void:
	_pending = false
	_set_waiting(false)
	if current == null or data.get("npc_id", "") != current.npc_id:
		return
	body.append_text("[color=#b0781a][b]%s[/b][/color]  %s\n" % [current.npc_name, data.get("text", "")])
	aff_bar.value = float(data.get("affinity", aff_bar.value))
	var change := float(data.get("affinity_change", 0))
	aff_label.text = "%s  %d/100  %s" % [data.get("level", ""), int(aff_bar.value),
		("+%d" % int(change)) if change > 0 else "%d" % int(change)]
	_set_affinity_text(data.get("level", ""), aff_bar.value, change)
	_append_change_note(change, data.get("level", ""), int(aff_bar.value), "送礼")
	current.set_emotion("开心" if data.get("liked") else ("难过" if data.get("disliked") else ""))

func _set_affinity_text(level: String, value: float, change: float) -> void:
	var delta := ""
	if change > 0.5:
		delta = "   +%d ↑" % int(round(change))
	elif change < -0.5:
		delta = "   %d ↓" % int(round(change))
	aff_label.text = "%s  %d/100%s" % [level, int(value), delta]
	var col := INK
	if change > 0.5:
		col = Color(0.72, 0.23, 0.25)      # 涨了：红
	elif change < -0.5:
		col = Color(0.20, 0.38, 0.60)      # 掉了：蓝
	aff_label.add_theme_color_override("font_color", col)

func _append_change_note(change: float, level: String, value: int, why: String) -> void:
	"""把这一轮的加减直接写进对话记录，一眼能看到。"""
	if absf(change) < 0.5:
		body.append_text("[color=#8a8780]　好感度不变（%s %d/100）[/color]\n" % [level, value])
	elif change > 0:
		body.append_text("[color=#b5342f]　好感度 +%d ↑　现在 %s %d/100%s[/color]\n" % [
			int(round(change)), level, value, ("（%s）" % why) if why != "" else ""])
	else:
		body.append_text("[color=#2f5f9e]　好感度 %d ↓　现在 %s %d/100%s[/color]\n" % [
			int(round(change)), level, value, ("（%s）" % why) if why != "" else ""])

func _on_inventory(items: Array) -> void:
	inventory = items

func _on_toast(text: String) -> void:
	if visible:
		body.append_text("[color=#3b6d11]※ %s[/color]\n" % text)
