# 输入回归测试：AITOWN_INPUTTEST=1，仅发送到计数替身，不联网调用模型。
extends Node

class DialogueProbe:
	extends "res://scripts/dialogue_ui.gd"
	var send_calls := 0
	var composing_override := false

	func _process(_delta: float) -> void:
		pass

	func _send() -> void:
		send_calls += 1

	func _ime_blocks_enter() -> bool:
		return composing_override or super._ime_blocks_enter()

var _checks := 0
var _failures := 0

func _check(condition: bool, label: String) -> void:
	_checks += 1
	if not condition:
		_failures += 1
	print("[INPUT] ", "PASS " if condition else "FAIL ", label)

func _enter() -> InputEventKey:
	var key := InputEventKey.new()
	key.keycode = KEY_ENTER
	key.physical_keycode = KEY_ENTER
	key.pressed = true
	return key

func _ready() -> void:
	await get_tree().process_frame
	var dialog := DialogueProbe.new()
	add_child(dialog)
	dialog.visible = true
	dialog.current = Node.new()
	dialog.input.text = "我想说 wana 的事情"
	dialog.input.grab_focus()
	await get_tree().process_frame

	dialog._track_ime_state(true)
	dialog.input.text_submitted.emit(dialog.input.text)
	await get_tree().process_frame
	_check(dialog.send_calls == 0, "合成期间的回车不发送")

	dialog._track_ime_state(false)
	dialog.input.text_submitted.emit(dialog.input.text)
	await get_tree().process_frame
	_check(dialog.send_calls == 0, "先上屏再清空合成的同次回车不发送")
	_check(dialog.input.text == "我想说 wana 的事情", "确认英文后输入框原文保留")

	# 模拟开始/结束通知都在一次事件队列中发生，帧轮询还没机会执行。
	dialog._reset_ime_guard()
	dialog._track_ime_state(true)
	dialog._track_ime_state(false)
	dialog.input.text_submitted.emit(dialog.input.text)
	await get_tree().process_frame
	_check(dialog.send_calls == 0, "同帧开始并结束的合成不误发")

	# 模拟提交回调发生后，原生队列才通知合成结束。
	dialog._reset_ime_guard()
	dialog.input.text_submitted.emit(dialog.input.text)
	dialog._track_ime_state(true)
	dialog._track_ime_state(false)
	await get_tree().process_frame
	_check(dialog.send_calls == 0, "延后发送捕获同帧原生上屏通知")

	dialog.composing_override = true
	var escape := InputEventKey.new()
	escape.keycode = KEY_ESCAPE
	escape.pressed = true
	dialog._input(escape)
	_check(dialog.visible, "候选状态 ESC 不关闭聊天界面")
	dialog.composing_override = false
	dialog._reset_ime_guard()

	dialog.input.grab_focus()
	await get_tree().process_frame
	get_viewport().push_input(_enter())
	await get_tree().process_frame
	await get_tree().process_frame
	_check(dialog.send_calls == 1, "普通回车通过 LineEdit 事件发送一次")
	_check(dialog.input.is_editing(), "回车后保留编辑状态")

	# 结束合成后允许普通发送，不长期禁用 Enter。
	dialog._track_ime_state(true)
	dialog._track_ime_state(false)
	await get_tree().create_timer(0.18).timeout
	dialog.input.text_submitted.emit(dialog.input.text)
	await get_tree().process_frame
	_check(dialog.send_calls == 2, "确认保护结束后下一次回车正常发送")

	dialog.composing_override = true
	dialog.send_btn.pressed.emit()
	_check(dialog.send_calls == 3, "显式点击发送按钮不被回车保护拦截")
	dialog.composing_override = false
	dialog.waiting = true
	dialog._reset_ime_guard()
	dialog.input.text_submitted.emit(dialog.input.text)
	await get_tree().process_frame
	_check(dialog.send_calls == 3, "等待回复期间回车不重复发送")

	dialog.waiting = false
	dialog.visible = false
	dialog.input.text_submitted.emit(dialog.input.text)
	await get_tree().process_frame
	_check(dialog.send_calls == 3, "已关闭聊天界面不发送")

	# 输入 e/Enter 不应再触发玩家的开聊逻辑。
	var player: Node = get_tree().get_first_node_in_group("player")
	var old_nearby: Node = player.nearby_npc
	var sentinel := Node.new() # 若 guard 失效，会触发访问不存在的 npc_name 报错。
	player.nearby_npc = sentinel
	player.set_interacting(true)
	player._input(_enter())
	var letter_e := InputEventKey.new()
	letter_e.keycode = KEY_E
	letter_e.pressed = true
	player._input(letter_e)
	_check(player.is_interacting, "对话中输入 E/Enter 不重新开聊")
	player.nearby_npc = old_nearby
	player.set_interacting(false)
	sentinel.free()

	dialog.current.free()
	dialog.current = null
	dialog.queue_free()
	await get_tree().process_frame
	print("[INPUT] RESULT ", _checks - _failures, "/", _checks)
	get_tree().quit(0 if _failures == 0 else 1)
