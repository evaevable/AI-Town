# 全局小工具（autoload 名 Config）。真正的服务器地址在 net.gd 里改。
extends Node

const DEBUG_MODE := true

func log_info(message: String) -> void:
	if DEBUG_MODE:
		print("[INFO] ", message)

func log_error(message: String) -> void:
	print("[ERROR] ", message)
