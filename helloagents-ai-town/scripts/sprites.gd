# 运行时生成角色动画：4 张角色图布局一致，按行/列切图。
extends RefCounted
class_name Sprites

const W := 48
const H := 70
const ROWS := {  # 每行 6 帧的行走动画
	"walk_right": Vector2(0, 792),
	"walk_up": Vector2(288, 792),
	"walk_left": Vector2(576, 792),
	"walk_down": Vector2(864, 792),
}
const STAND := {  # 站立帧
	"idle_down": Vector2(144, 24),
	"idle_right": Vector2(0, 24),
	"idle_up": Vector2(48, 24),
	"idle_left": Vector2(96, 24),
}

static func build(sprite_name: String) -> SpriteFrames:
	var path := "res://assets/characters/%s.png" % sprite_name
	var tex: Texture2D = load(path) if ResourceLoader.exists(path) else null
	var frames := SpriteFrames.new()
	if tex == null:
		return frames
	frames.remove_animation("default")
	for anim in ROWS.keys():
		frames.add_animation(anim)
		frames.set_animation_loop(anim, true)
		frames.set_animation_speed(anim, 8.0)
		var base: Vector2 = ROWS[anim]
		for i in 6:
			var at := AtlasTexture.new()
			at.atlas = tex
			at.region = Rect2(base.x + i * W, base.y, W, H)
			frames.add_frame(anim, at)
	for anim in STAND.keys():
		frames.add_animation(anim)
		frames.set_animation_loop(anim, false)
		var at2 := AtlasTexture.new()
		at2.atlas = tex
		at2.region = Rect2(STAND[anim].x, STAND[anim].y, W, H)
		frames.add_frame(anim, at2)
	return frames
