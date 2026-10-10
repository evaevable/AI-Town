# HUD 回归测试：固定屏幕坐标、相机移动/缩放、窗口缩放、4 人关系和层级。
# AITOWN_HUDTEST=1，测试只修改客户端显示，不发聊天、不改后端存档。
extends Node

var _checks := 0
var _failures := 0

func _check(ok: bool, label: String) -> void:
	_checks += 1
	if not ok:
		_failures += 1
	print("[HUD] ", "PASS " if ok else "FAIL ", label)

func _screen_rect(control: Control) -> Rect2:
	return control.get_global_transform_with_canvas() * Rect2(Vector2.ZERO, control.size)

func _settle() -> void:
	for i in 5:
		await get_tree().process_frame

func _ready() -> void:
	await _settle()
	var main: Node = get_parent()
	# 隔离服务器异步状态，以确定性数据验证；没有任何数据写回后端。
	Net.set_process(false)
	var player: Node2D = get_tree().get_first_node_in_group("player")
	var camera: Camera2D = player.get_node("Camera2D")
	main._on_clock({"date": "10月28日", "day": 58, "time": "20:32", "segment": "晚上"})
	main._on_player_state({"affinities": [
		{"npc_id": "qiqi", "name": "柒柒", "level": "亲密", "affinity": 70},
		{"npc_id": "zebuyi", "name": "泽不易", "level": "友好", "affinity": 46},
		{"npc_id": "qianqian", "name": "谦谦", "level": "熟悉", "affinity": 28}
	]})
	main._on_quests([])
	await _settle()
	var panel: Control = main.relation_box.get_parent()
	var relation_rect := _screen_rect(panel)
	var help_rect := _screen_rect(main.hud_help)
	var clock_rect := _screen_rect(main.clock_label.get_parent().get_parent())
	_check(main.hud_layer.layer > 0 and not main.hud_layer.follow_viewport_enabled,
		"HUD 使用独立屏幕画布，不跟随世界相机")
	_check(main.dialogue.layer > main.hud_layer.layer, "对话框与遮罩位于 HUD 上方")
	_check(main.hud.size.is_equal_approx(main.get_viewport_rect().size), "HUD 根节点填满视口")
	_check(clock_rect.end.y <= relation_rect.position.y, "时钟和关系面板纵向堆叠不重叠")
	_check(main.relation_box.get_child_count() == 4, "关系面板显示标题和三个 NPC")

	var original_position := player.global_position
	var original_zoom := camera.zoom
	for point in [Vector2(60, 420), Vector2(650, 480), Vector2(1180, 268)]:
		player.global_position = point
		camera.force_update_scroll()
		await _settle()
		_check(_screen_rect(panel).position.is_equal_approx(relation_rect.position),
			"玩家移动到 %s 后关系面板仍固定" % point)
		_check(_screen_rect(main.hud_help).position.is_equal_approx(help_rect.position),
			"玩家移动后操作说明仍固定")

	camera.zoom = Vector2(2, 2)
	camera.force_update_scroll()
	await _settle()
	_check(_screen_rect(panel).is_equal_approx(relation_rect), "改变相机缩放不改变 HUD 尺寸和坐标")
	camera.zoom = original_zoom

	# 屏幕图层和世界图层独立，NPC 的 z_index 再大也不会盖住 HUD。
	var actor: Node2D = Node2D.new()
	main.add_child(actor)
	actor.z_index = 4096
	_check(actor.get_canvas() != panel.get_canvas(), "世界角色和好感度面板处于不同画布")
	actor.queue_free()

	main._on_chat_end_affinity({"npc_id": "qiqi", "affinity": 78, "level": "亲密"})
	await _settle()
	var first_row: Control = main.relation_box.get_child(1)
	_check(first_row.get_child(2).text == "亲密 78/100", "聊天后好感度数值即时刷新")
	main.affinity_cache["test_fourth"] = {"name": "第四位", "affinity": 51, "level": "友好"}
	main._render_relations()
	main._on_quests([{"content": "帮柒柒给泽不易带句话"}])
	await _settle()
	_check(main.relation_box.get_child_count() == 5, "第四个 NPC 能显示且刷新时不叠加旧行")
	_check(_screen_rect(panel).end.y <= _screen_rect(main.hud_quests).position.y,
		"第四人加入后委托自动下移、不盖住好感度")

	main.get_window().mode = Window.MODE_WINDOWED
	for screen_size in [Vector2i(960, 540), Vector2i(1440, 900)]:
		main.get_window().size = screen_size
		await _settle()
		main._layout_hud()
		await _settle()
		var visible_area: Rect2 = main.get_viewport_rect()
		_check(visible_area.encloses(_screen_rect(panel)), "窗口 %s 下好感度仍在可见范围" % screen_size)
		_check(visible_area.encloses(_screen_rect(main.hud_help)), "窗口 %s 下操作说明仍在右上角" % screen_size)
		_check(not _screen_rect(panel).intersects(_screen_rect(main.hud_help)), "左右面板不重叠")

	main.affinity_cache.erase("test_fourth")
	main._on_chat_end_affinity({"npc_id": "qiqi", "affinity": 70, "level": "亲密"})
	main._on_quests([])
	main.get_window().size = Vector2i(1280, 720)
	# 对应用户第二张截图：玩家在屋子中间，镜头已经远离场景原点。
	player.global_position = Vector2(560, 480)
	camera.force_update_scroll()
	await _settle()
	if DisplayServer.get_name() != "headless":
		await RenderingServer.frame_post_draw
		var output := OS.get_environment("AITOWN_HUD_CAPTURE")
		if not output.is_empty():
			var image := get_viewport().get_texture().get_image()
			var error := image.save_png(output)
			_check(error == OK, "生成移动后游戏实景截图")
	player.global_position = original_position
	print("[HUD] RESULT ", _checks - _failures, "/", _checks)
	get_tree().quit(0 if _failures == 0 else 1)
