# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["pyinstaller_entry.py"],
    pathex=[],
    binaries=[],
    datas=[
        # Web UI assets (HTML, JS, CSS, vendor libs)
        ("echobot/app/web", "echobot/app/web"),
        # Built-in Live2D models
        ("echobot/app/builtin_live2d", "echobot/app/builtin_live2d"),
        # Built-in stage backgrounds
        ("echobot/app/builtin_stage_backgrounds", "echobot/app/builtin_stage_backgrounds"),
        # Built-in skills (SKILL.md, templates, schemas, etc.)
        ("echobot/skills", "echobot/skills"),
    ],
    hiddenimports=[
        # uvicorn internals
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # multipart (used by FastAPI file uploads)
        "multipart",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Qt bindings conflict — not used by EchoBot
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        # GUI toolkits not needed for a web server
        "tkinter",
        "_tkinter",
        # matplotlib pulled in transitively, not used at runtime
        "matplotlib",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="echobot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="echobot",
)
