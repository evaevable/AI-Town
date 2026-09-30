# NPC：按后端配置生成，沿路点寻路移动，头顶显示独白/台词/情绪，可被玩家按 E 交互。
extends CharacterBody2D

@export var npc_name: String = ""
@export var npc_title: String = ""

var npc_id: String = ""
var place: String = "走廊"          # 当前所在地点名（由后端决定）
var behavior: Dictionary = {}
var emotion: String = ""
var is_interacting: bool = false

@onready var animated_sprite: AnimatedSprite2D = $AnimatedSprite2D
@onready var interaction_area: Area2D = $InteractionArea
@onready var name_label: Label = $NameLabel
@onready var dialogue_label: Label = $DialogueLabel

var emotion_label: Label
var _path: Array = []
var _walk_timer := 0.0
var _bubble_timer := 0.0
var _idle_anim := "idle_down"
var _last_pos := Vector2.ZERO
var _stuck := 0.0
var _pass_through := false
var _sprite_name := "character_2"

func _ready() -> void:
	add_to_group("npcs")
	_last_pos = global_position
	if npc_name == "":
		npc_name = name
	name_label.text = npc_name
	dialogue_label.text = ""
	dialogue_label.visible = false
	interaction_area.body_entered.connect(_on_body_entered)
	interaction_area.body_exited.connect(_on_body_exited)
	_build_emotion_label()
	if animated_sprite.sprite_frames and animated_sprite.sprite_frames.has_animation(_idle_anim):
		animated_sprite.play(_idle_anim)

func _build_emotion_label() -> void:
	emotion_label = Label.new()
	emotion_label.name = "EmotionLabel"
	emotion_label.position = Vector2(-40, -112)
	emotion_label.size = Vector2(96, 26)
	emotion_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	emotion_label.add_theme_color_override("font_color", Color(0.93, 0.42, 0.6))
	emotion_label.add_theme_color_override("font_outline_color", Color(1, 1, 1, 0.9))
	emotion_label.add_theme_constant_override("outline_size", 7)
	emotion_label.text = ""
	add_child(emotion_label)

# ---------- 由 main.gd 调用 ----------
func setup(info: Dictionary) -> void:
	npc_id = str(info.get("id", ""))
	npc_name = str(info.get("name", npc_name))
	npc_title = str(info.get("title", ""))
	place = str(info.get("spawn", "走廊"))
	behavior = info.get("behavior", {})
	_sprite_name = str(info.get("sprite", "character_2"))
	name_label.text = npc_name
	var frames := Sprites.build(_sprite_name)
	if frames.get_animation_names().size() > 0:
		animated_sprite.sprite_frames = frames
		animated_sprite.play(_idle_anim)
	var tint := str(info.get("tint", "#ffffff"))
	if tint != "#ffffff" and tint != "":
		animated_sprite.modulate = Color(tint)

func goto_place(new_place: String) -> void:
	if new_place == place:
		return
	place = new_place
	_path = WorldMap.path(_current_node(), place)
	behavior["wander"] = behavior.get("wander", true)

func _current_node() -> String:
	var best := place
	var best_d := 999999.0
	for k in WorldMap.POINTS.keys():
		var d: float = global_position.distance_to(WorldMap.POINTS[k])
		if d < best_d:
			best_d = d
			best = str(k)
	return best

func show_bubble(text: String, seconds: float = 8.0) -> void:
	if text.strip_edges() == "":
		return
	dialogue_label.text = text
	dialogue_label.visible = true
	_bubble_timer = seconds

func set_emotion(value: String) -> void:
	emotion = value
	if emotion_label:
		var pool := ["开心", "害羞", "惊讶", "生气", "难过", "无语", "得意", "思考"]
		emotion_label.text = value if value in pool else ""

func set_interacting(v: bool) -> void:
	is_interacting = v
	_path.clear()
	velocity = Vector2.ZERO

func place_name() -> String:
	return place

# ---------- 物理 ----------
func _physics_process(delta: float) -> void:
	if _bubble_timer > 0.0:
		_bubble_timer -= delta
		if _bubble_timer <= 0.0:
			dialogue_label.visible = false

	if is_interacting:
		velocity = Vector2.ZERO
		move_and_slide()
		_play("idle_down")
		return

	var target := Vector2.ZERO
	if _path.size() > 0:
		target = _path[0]
		if global_position.distance_to(target) < 12.0:
			_path.pop_front()
			if _path.is_empty():
				_play(_idle_anim)
			return
	else:
		if not behavior.get("wander", true):
			_play(_idle_anim)
			return
		_walk_timer -= delta
		if _walk_timer <= 0.0:
			_walk_timer = randf_range(2.5, 6.0)
			var radius: float = float(behavior.get("wander_radius", 60))
			var base: Vector2 = WorldMap.point(place)
			_path = [base + Vector2(randf_range(-radius, radius), randf_range(-radius * 0.6, radius * 0.6))]
		else:
			_play(_idle_anim)
			return

	var dir := target - global_position
	if dir.length() < 1.0:
		return
	dir = dir.normalized()
	velocity = dir * float(behavior.get("move_speed", 60))
	move_and_slide()
	_update_anim(dir)
	z_index = int(global_position.y / 4.0)
	_check_stuck(delta)

func _check_stuck(delta: float) -> void:
	# 撞在墙上时短暂关掉碰撞穿过去，避免 NPC 永远卡在角落
	if global_position.distance_to(_last_pos) < 0.5:
		_stuck += delta
		if _stuck > 1.5 and not _pass_through:
			_pass_through = true
			set_collision_mask_value(1, false)
	else:
		_stuck = 0.0
		if _pass_through:
			_pass_through = false
			set_collision_mask_value(1, true)
	_last_pos = global_position

func _play(anim: String) -> void:
	if animated_sprite.sprite_frames and animated_sprite.sprite_frames.has_animation(anim):
		if animated_sprite.animation != anim:
			animated_sprite.play(anim)

func _update_anim(dir: Vector2) -> void:
	var anim := _idle_anim
	if absf(dir.x) > absf(dir.y):
		anim = "walk_right" if dir.x > 0 else "walk_left"
		_idle_anim = "idle_right" if dir.x > 0 else "idle_left"
		animated_sprite.flip_h = false
	else:
		anim = "walk_down" if dir.y > 0 else "walk_up"
		_idle_anim = "idle_down" if dir.y > 0 else "idle_up"
	_play(anim)

# ---------- 交互 ----------
func _on_body_entered(body: Node2D) -> void:
	if body.is_in_group("player"):
		if body.has_method("set_nearby_npc"):
			body.set_nearby_npc(self)
		Net.notify_near(npc_id)

func _on_body_exited(body: Node2D) -> void:
	if body.is_in_group("player") and body.has_method("set_nearby_npc"):
		body.set_nearby_npc(null)
