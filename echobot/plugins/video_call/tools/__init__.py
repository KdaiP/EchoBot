"""视频通话插件 - 大模型工具定义"""

VISION_TOOLS = [
    {
        "name": "bind_face_to_name",
        "description": (
            "将当前画面中检测到的人脸与指定姓名绑定。"
            "你见到用户时并在上下文中获取到了用户姓，并且能确定不会是别人时调用此工具。"
            "调用后，后续每次检测到该人脸都会自动识别为该姓名。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "person_name": {
                    "type": "string",
                    "description": "要绑定的人物姓名",
                },
            },
            "required": ["person_name"],
        },
    },
    {
        "name": "list_known_faces",
        "description": "列出所有已绑定姓名的人脸。当用户问'你认识哪些人'、'你记住了谁'时调用。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "forget_face",
        "description": "删除某个人脸绑定。当用户说'忘掉XXX'、'删除XXX的人脸'时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "person_name": {
                    "type": "string",
                    "description": "要删除的人物姓名",
                },
            },
            "required": ["person_name"],
        },
    },
]
