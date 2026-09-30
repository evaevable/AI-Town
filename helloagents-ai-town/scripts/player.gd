# 玩家控制：WASD 移动、E 交互，带卡位自救。
extends CharacterBody2D

@export var speed: float = 200.0

var nearby_npc: Node = null
var is_interacting: bool = false

@onready var animated_sprite: AnimatedSprite2D = $AnimatedSprite2D
@onready var camera: Camera2D = $Camera2D

@onready var interact_sound: AudioStreamPlayer = null
@onready var running_sound: AudioStreamPlayer = null
var is_playing_running_sound: bool = false

# 卡位自救
var _last_pos := Vector2.ZERO
var _stuck_time := 0.0
var _nudged := false

func _ready() -> void:
	add_to_group("player")
	interact_sound = get_node_or_null("InteractSound")
	running_sound = get_node_or_null("RunningSound")
	_last_pos = global_position
	if interact_sound:
		print("[INFO] 玩家交互音效已启用")
	if running_sound:
		print("[INFO] 玩家走路音效已启用")
	Config.log_info("玩家初始化完成")
	camera.enabled = true
	if animated_sprite.sprite_frames != null and animated_sprite.sprite_frames.has_animation("idle"):
		animated_sprite.play("idle")

func _physics_process(delta: float) -> void:
	if is_interacting:
		velocity = Vector2.ZERO
		move_and_slide()
		if animated_sprite.sprite_frames != null and animated_sprite.sprite_frames.has_animation("idle"):
			animated_sprite.play("idle")
		stop_running_sound()
		return

	var input_direction := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
	velocity = input_direction * speed
	move_and_slide()
	update_animation(input_direction)
	update_running_sound(input_direction)
	_check_stuck(delta, input_direction)

func _check_stuck(delta: float, input_direction: Vector2) -> void:
	"""按着方向键却几乎没动 = 卡住了：先退一点，再不行就挪到最近的路点。"""
	if input_direction.length() < 0.1:
		_stuck_time = 0.0
		_nudged = false
		_last_pos = global_position
		return
	if global_position.distance_to(_last_pos) > 0.8:
		_stuck_time = 0.0
		_nudged = false
	else:
		_stuck_time += delta
		if _stuck_time > 1.0 and not _nudged:
			_nudged = true
			# 先往反方向退一小步，多数情况这样就能脱身
			global_position -= input_direction.normalized() * 8.0
		elif _stuck_time > 2.4:
			_rescue()
	_last_pos = global_position

func _rescue() -> void:
	var best := Vector2.ZERO
	var best_d := 999999.0
	for k in WorldMap.POINTS.keys():
		var p: Vector2 = WorldMap.POINTS[k]
		var d := global_position.distance_to(p)
		if d < best_d:
			best_d = d
			best = p
	global_position = best
	_stuck_time = 0.0
	_nudged = false
	_last_pos = global_position
	print("[INFO] 玩家卡住了，已挪到最近的位置: ", best)
	get_tree().call_group("main", "show_toast", "这里走不通，我帮你挪了一下")

func update_animation(direction: Vector2) -> void:
	if animated_sprite.sprite_frames == null:
		return
	if direction.length() > 0:
		if absf(direction.x) > absf(direction.y):
			if direction.x > 0:
				if animated_sprite.sprite_frames.has_animation("walk_right"):
					animated_sprite.play("walk_right")
					animated_sprite.flip_h = false
				elif animated_sprite.sprite_frames.has_animation("walk"):
					animated_sprite.play("walk")
					animated_sprite.flip_h = false
			else:
				if animated_sprite.sprite_frames.has_animation("walk_left"):
					animated_sprite.play("walk_left")
					animated_sprite.flip_h = false
				elif animated_sprite.sprite_frames.has_animation("walk"):
					animated_sprite.play("walk")
					animated_sprite.flip_h = true
		else:
			if direction.y > 0:
				if animated_sprite.sprite_frames.has_animation("walk_down"):
					animated_sprite.play("walk_down")
				elif animated_sprite.sprite_frames.has_animation("walk"):
					animated_sprite.play("walk")
			else:
				if animated_sprite.sprite_frames.has_animation("walk_up"):
					animated_sprite.play("walk_up")
				elif animated_sprite.sprite_frames.has_animation("walk"):
					animated_sprite.play("walk")
	else:
		if animated_sprite.sprite_frames.has_animation("idle"):
			animated_sprite.play("idle")

func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_E or event.keycode == KEY_ENTER:
			if nearby_npc != null:
				interact_with_npc()

func interact_with_npc() -> void:
	if nearby_npc == null:
		return
	if interact_sound:
		interact_sound.play()
	Config.log_info("与NPC交互: " + nearby_npc.npc_name)
	get_tree().call_group("dialogue_system", "start_dialogue", nearby_npc.npc_name)

func set_nearby_npc(npc: Node) -> void:
	nearby_npc = npc
	if npc != null:
		print("[INFO] ✅ 进入NPC范围: ", npc.npc_name)
	else:
		print("[INFO] ❌ 离开NPC范围")

func get_nearby_npc() -> Node:
	return nearby_npc

func set_interacting(interacting: bool) -> void:
	is_interacting = interacting
	if interacting:
		stop_running_sound()

func update_running_sound(direction: Vector2) -> void:
	if running_sound == null:
		return
	if direction.length() > 0:
		if not is_playing_running_sound:
			running_sound.play()
			is_playing_running_sound = true
	else:
		stop_running_sound()

func stop_running_sound() -> void:
	if running_sound and is_playing_running_sound:
		running_sound.stop()
		is_playing_running_sound = false
