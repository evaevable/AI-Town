# 网络层：WebSocket 长连接 + REST 辅助。挂在 autoload 名字 Net 上。
extends Node

# ---------- 配置 ----------
const SERVER = "127.0.0.1"
const PORT = 8010
const HTTP_BASE = "http://127.0.0.1:8010"
const WS_URL = "ws://127.0.0.1:8010/ws"

# ---------- 信号 ----------
signal connected()
signal disconnected()
signal welcome(npcs: Array, clock: Dictionary, event: String)
signal npcs_changed(npcs: Array)
signal npc_move(data: Dictionary)
signal bubble(data: Dictionary)
signal npc_chat(data: Dictionary)
signal clock_tick(data: Dictionary)
signal world_event(data: Dictionary)
signal toast(text: String)
signal chat_start(data: Dictionary)
signal chat_delta(data: Dictionary)
signal chat_replace(data: Dictionary)
signal chat_end(data: Dictionary)
signal gift_result(data: Dictionary)
signal inventory(items: Array)
signal quests(list: Array)
signal player_state(data: Dictionary)

var socket := WebSocketPeer.new()
var _http_player: HTTPRequest
var _connected := false
var _retry := 0.0
var _closed_once := false
var _http: HTTPRequest

func _ready() -> void:
	_http = HTTPRequest.new()
	add_child(_http)
	_http_player = HTTPRequest.new()
	add_child(_http_player)
	_http_player.request_completed.connect(_on_player_state)
	_try_connect()

func fetch_player() -> void:
	_http_player.request(HTTP_BASE + "/api/player")

func _on_player_state(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if code != 200:
		return
	var json := JSON.new()
	if json.parse(body.get_string_from_utf8()) == OK and json.data is Dictionary:
		player_state.emit(json.data)

func _try_connect() -> void:
	var err := socket.connect_to_url(WS_URL)
	print("[NET] connect_to_url err=", err, " url=", WS_URL)
	if err != OK:
		print("[NET] 连接失败: ", error_string(err))

func _process(delta: float) -> void:
	socket.poll()
	var state := socket.get_ready_state()
	if state == WebSocketPeer.STATE_OPEN:
		if not _connected:
			_connected = true
			_retry = 0.0
			connected.emit()
			print("[NET] 已连接后端")
		while socket.get_available_packet_count() > 0:
			_handle(socket.get_packet().get_string_from_utf8())
	elif state == WebSocketPeer.STATE_CLOSED:
		if _closed_once == false:
			_closed_once = true
			print("[NET] 连接关闭 code=", socket.get_close_code(), " reason=", socket.get_close_reason())
		if _connected:
			_connected = false
			disconnected.emit()
			print("[NET] 后端断开")
		_retry -= delta
		if _retry <= 0.0:
			_retry = 2.0
			_try_connect()

func is_online() -> bool:
	return _connected

func send_msg(dict: Dictionary) -> void:
	if socket.get_ready_state() == WebSocketPeer.STATE_OPEN:
		socket.send_text(JSON.stringify(dict))

# ---------- 对外接口 ----------
func say(npc_id: String, message: String) -> void:
	send_msg({"type": "chat", "npc_id": npc_id, "message": message})

func gift(npc_id: String, item: String) -> void:
	send_msg({"type": "gift", "npc_id": npc_id, "item": item})

func notify_near(npc_id: String) -> void:
	send_msg({"type": "near", "npc_id": npc_id})

func _handle(text: String) -> void:
	var json := JSON.new()
	if json.parse(text) != OK:
		return
	var data = json.data
	if OS.get_environment("AITOWN_AUTOTEST") != "":
		print("[NET] recv type=", data.get("type", ""), " npc=", data.get("npc_id", ""))
	match data.get("type", ""):
		"welcome":
			welcome.emit(data.get("npcs", []), data.get("clock", {}), data.get("event", ""))
		"npcs_changed":
			npcs_changed.emit(data.get("npcs", []))
		"npc_move":
			npc_move.emit(data)
		"bubble":
			bubble.emit(data)
		"npc_chat":
			npc_chat.emit(data)
		"clock":
			clock_tick.emit(data)
		"event":
			world_event.emit(data)
		"toast":
			toast.emit(data.get("text", ""))
		"chat_start":
			chat_start.emit(data)
		"chat_delta":
			chat_delta.emit(data)
		"chat_replace":
			chat_replace.emit(data)
		"chat_end":
			chat_end.emit(data)
		"gift_result":
			gift_result.emit(data)
		"inventory":
			inventory.emit(data.get("items", []))
		"quests":
			quests.emit(data.get("list", data.get("quests", [])))
