# EchoBot Desktop

这是 EchoBot 的最小 Electron 桌宠壳。

## 当前能力

- 自动启动 Python 后端：`python -m echobot app --host 127.0.0.1 --port 8000`
- 打开透明桌宠窗口：加载 `http://127.0.0.1:8000/desktop`
- 托盘菜单
- 打开控制面板：跳转 `http://127.0.0.1:8000/web`
- macOS 下桌宠会尽量保持在所有工作区与全屏应用之上

## 启动方式

1. 进入目录：

```bash
cd desktop
```

2. 安装依赖：

```bash
npm install
```

3. 启动桌面端：

```bash
npm start
```