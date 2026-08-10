#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黑盒语音 · UTC 时间频道名自动更新机器人
========================================
把房间里的一个文字频道的名称实时更新为当前 UTC 时间，格式：UTC HH:mm

设计要点：
- 零第三方依赖（只使用 Python 标准库 urllib），可直接跑在 GitHub Actions
  的 ubuntu-latest 上（自带 python3）。
- 三种运行模式：
    * --once       ：更新一次后退出（配合 GitHub Actions cron 每 5 分钟触发）
    * --loop       ：每分钟更新，持续 LOOP_MINUTES 分钟后自行退出
                     （配合 Actions 常驻循环 job 实现真正的"每分钟"，cron 每 6 小时重启一次）
    * --list-rooms ：列出机器人已加入的房间（用于确认真实的 room_id）

环境变量（不要写死到代码里！）：
    HEYBOX_TOKEN   必填. 机器人 token，存到 GitHub Secrets
    ROOM_ID        必填. 房间真实 ID（19 位数字）。注意：不是 6 位房间号！
                    用 --list-rooms 对照房间名查出真实 ID
    CHANNEL_ID     可选. 文字频道 ID。留空则尝试自动创建；自动创建失败时请手动创建后填入
    CHANNEL_PREFIX 可选. 频道名前缀，默认 "UTC"，最终频道名形如 "UTC 14:35"
    LOOP_MINUTES   可选. loop 模式运行分钟数，默认 350（必须小于 Actions job 的 6 小时上限）
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE_URL = "https://chat.xiaoheihe.cn"

# 黑盒语音机器人 HTTP 接口（来源：官方 Apifox 文档）
API_PATH_EDIT = "/chatroom/v2/channel/edit"        # 频道名编辑（需"编辑频道"权限）
API_PATH_CREATE = "/chatroom/v2/channel/create"    # 创建频道（尽力而为，接口路径如有变动请看官方文档）
API_PATH_ROOMS = "/chatroom/v2/room/joined"        # 分页获取加入的房间列表

# 官方文档要求的固定 query 参数（声明是机器人客户端）
QUERY = {
    "client_type": "heybox_chat",
    "x_client_type": "web",
    "os_type": "web",
    "x_os_type": "bot",
    "x_app": "heybox_chat",
    "chat_os_type": "bot",
    "chat_version": "1.30.0",
}

TOKEN = os.environ.get("HEYBOX_TOKEN", "").strip()
ROOM_ID = os.environ.get("ROOM_ID", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
PREFIX = os.environ.get("CHANNEL_PREFIX", "UTC").strip()
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "350"))


def api(path, method="GET", body=None):
    """通用请求：token 放 header，固定 query 参数。返回解析后的 JSON。"""
    url = BASE_URL + path + "?" + urllib.parse.urlencode(QUERY)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("token", TOKEN)
    if data is not None:
        req.add_header("Content-Type", "application/json;charset=utf-8")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def utc_now_hhmm():
    """当前 UTC 时间，格式 HH:mm（阿拉伯数字）"""
    return datetime.now(timezone.utc).strftime("%H:%M")


def edit_channel_name(room_id, channel_id, name):
    """把频道名改成 name（POST /chatroom/v2/channel/edit）"""
    body = {
        "room_id": str(room_id),
        "channel_id": str(channel_id),
        "channel_name": name,
        "channel_type": 1,  # 1 = 文字频道
    }
    r = api(API_PATH_EDIT, "POST", body)
    if r.get("status") != "ok":
        raise RuntimeError(f"编辑频道名失败，接口返回: {r}")
    return r


def create_channel(room_id, name):
    """尽力自动创建文字频道。若接口路径变动/权限不足会抛错，请手动创建后填 CHANNEL_ID。"""
    body = {
        "room_id": str(room_id),
        "channel_name": name,
        "channel_type": 1,
    }
    r = api(API_PATH_CREATE, "POST", body)
    if r.get("status") != "ok":
        raise RuntimeError(f"创建频道失败，接口返回: {r}")
    data = r.get("result") or {}
    cid = data.get("channel_id") or data.get("id") or data.get("channel") or ""
    if isinstance(cid, dict):  # 兼容嵌套结构
        cid = cid.get("id", "")
    return str(cid)


def list_rooms():
    """列出机器人已加入的房间，帮助用户找到 19 位真实 room_id"""
    r = api(API_PATH_ROOMS, "GET")
    if r.get("status") != "ok":
        raise RuntimeError(f"获取房间列表失败，接口返回: {r}")
    print("机器人已加入的房间（注意：真实 room_id 是 19 位数字，不是 6 位房间号）：")
    print(f"{'room_id':<22} room_name")
    print("-" * 70)
    rooms = ((r.get("result") or {}).get("rooms") or {}).get("rooms") or []
    for room in rooms:
        print(f"{str(room.get('room_id', '')):<22} {room.get('room_name', '')}")
    if not rooms:
        print("（没有查到任何房间：请确认机器人已被邀请进房间、token 有效、有查看频道权限）")


def ensure_channel():
    """确保 CHANNEL_ID 可用：已配置则直接返回；未配置则尝试自动创建。"""
    global CHANNEL_ID
    if CHANNEL_ID:
        print(f"使用已配置的频道: {CHANNEL_ID}")
        return
    print("未配置 CHANNEL_ID，尝试自动创建文字频道 ...")
    try:
        cid = create_channel(ROOM_ID, f"{PREFIX} {utc_now_hhmm()}")
        if cid:
            CHANNEL_ID = cid
            print(f"自动创建成功！频道 ID: {cid}")
            print("提示：请把该 ID 填到 GitHub Actions 的变量 CHANNEL_ID，避免每次重复创建。")
            return
    except Exception as e:
        print(f"自动创建失败: {e}")
    print("=" * 70)
    print("请手动完成：在黑盒语音客户端进入房间 -> 新建一个文字频道（名字随意）")
    print("然后把频道的真实 ID 填入 GitHub Actions 变量 CHANNEL_ID，再重新触发运行。")
    print("（若确定是接口路径问题，可在本脚本顶部 API_PATH_CREATE 处按官方文档修正）")
    print("=" * 70)
    sys.exit(1)


def run_once():
    """单次更新（供 cron 每 5 分钟触发）"""
    ensure_channel()
    name = f"{PREFIX} {utc_now_hhmm()}"
    edit_channel_name(ROOM_ID, CHANNEL_ID, name)
    print(f"[{utc_now_hhmm()}] 已更新频道名为: {name}")


def run_loop():
    """循环模式：每分钟更新一次，运行 LOOP_MINUTES 分钟后自行退出"""
    ensure_channel()
    deadline = time.time() + LOOP_MINUTES * 60
    last = None
    while time.time() < deadline:
        now = utc_now_hhmm()
        if now != last:  # 分钟变化了才调接口，减少无效请求
            name = f"{PREFIX} {now}"
            edit_channel_name(ROOM_ID, CHANNEL_ID, name)
            last = now
            print(f"[{now}] 已更新频道名为: {name}")
        time.sleep(60)
    print(f"loop 模式已运行 {LOOP_MINUTES} 分钟，正常退出（等待 GitHub Actions 下一次调度唤醒）")


def main():
    if not TOKEN:
        print("缺少环境变量 HEYBOX_TOKEN，请先在 GitHub 仓库 Secrets 中配置")
        sys.exit(1)
    if not ROOM_ID:
        print("缺少环境变量 ROOM_ID，请先在 GitHub 仓库 Variables 中配置（19 位真实房间 ID）")
        sys.exit(1)
    arg = sys.argv[1] if len(sys.argv) > 1 else "--once"
    if arg == "--list-rooms":
        list_rooms()
    elif arg == "--loop":
        run_loop()
    else:
        run_once()


if __name__ == "__main__":
    main()
