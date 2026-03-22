/**
 * EchoBot 视频通话集成模块
 * 与原项目无缝集成，通过拉取视觉数据注入上下文
 */

export function createVideoCallIntegration(deps) {
    const {
        addSystemMessage,
        requestJson,
    } = deps;

    let isVideoCallEnabled = false;
    let videoStream = null;
    let videoCanvas = null;
    let videoCtx = null;
    let websocket = null;
    let isRecording = false;

    /**
     * 初始化视频通话集成
     */
    function initialize() {
        // 绑定设置面板开关
        const toggle = document.getElementById('video-call-toggle');
        if (toggle) {
            // 恢复上次保存的状态
            const saved = localStorage.getItem('echobot.video_call_enabled') === 'true';
            toggle.checked = saved;
            isVideoCallEnabled = saved;
            _applyVideoBarVisibility(saved);

            toggle.addEventListener('change', (e) => {
                isVideoCallEnabled = e.target.checked;
                _applyVideoBarVisibility(isVideoCallEnabled);
                if (!isVideoCallEnabled) {
                    stopVideoCall();
                }
                localStorage.setItem('echobot.video_call_enabled', isVideoCallEnabled);
            });
        }

        // 绑定视频通话按钮
        const startBtn = document.getElementById('start-video-call-btn');
        const stopBtn = document.getElementById('stop-video-call-btn');
        if (startBtn) {
            startBtn.addEventListener('click', startVideoCall);
            console.log('✓ video start button bound');
        } else {
            console.warn('✗ start-video-call-btn not found');
        }
        if (stopBtn) stopBtn.addEventListener('click', stopVideoCall);

        // 暴露到全局，供 chat.js 调用
        window.__videoCall = {
            isEnabled: () => isVideoCallEnabled,
            getVisionContext,
        };
    }

    /**
     * 控制视频通话栏的显示/隐藏
     */
    function _applyVideoBarVisibility(visible) {
        const bar = document.getElementById('video-call-bar');
        if (bar) bar.hidden = !visible;
    }

    /**
     * 启动视频通话
     */
    async function startVideoCall() {
        try {
            // 检查浏览器支持
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                addSystemMessage('✗ 浏览器不支持摄像头，请使用 Chrome/Edge 并通过 http://localhost 访问');
                return;
            }

            addSystemMessage('正在请求摄像头权限...');
            videoStream = await navigator.mediaDevices.getUserMedia({
                video: { width: 1280, height: 720 },
                audio: false,
            });

            videoCanvas = document.createElement('canvas');
            videoCanvas.width = 1280;
            videoCanvas.height = 720;
            videoCtx = videoCanvas.getContext('2d');

            const videoEl = document.createElement('video');
            videoEl.srcObject = videoStream;
            videoEl.muted = true;
            await videoEl.play();

            // 显示摄像头预览小窗口
            _showPreview(videoEl);

            addSystemMessage('正在连接视频服务...');
            await _connectWebSocket();

            isRecording = true;
            _captureFrames(videoEl);
            _startVisionDebugPanel();

            _updateVideoUI(true);
            addSystemMessage('✓ 视频通话已启动，每秒发送一帧图像');
        } catch (error) {
            const hints = {
                'NotFoundError': '未找到摄像头设备，请检查摄像头是否已连接',
                'NotAllowedError': '摄像头权限被拒绝，请在浏览器/系统设置中允许摄像头访问',
                'NotReadableError': '摄像头被其他应用占用，请关闭 Zoom/FaceTime 等应用后重试',
            };
            const hint = hints[error.name] || error.message;
            addSystemMessage(`✗ 视频启动失败：${hint}`);
            console.error('startVideoCall error:', error);
        }
    }

    /**
     * 停止视频通话
     */
    function stopVideoCall() {
        isRecording = false;

        if (videoStream) {
            videoStream.getTracks().forEach((t) => t.stop());
            videoStream = null;
        }
        if (websocket) {
            websocket.close();
            websocket = null;
        }

        // 移除预览窗口和调试面板
        document.getElementById('__cam-preview')?.remove();
        document.getElementById('__vision-debug')?.remove();

        _updateVideoUI(false);
    }

    /**
     * 显示摄像头预览小窗口（右下角悬浮）
     */
    function _showPreview(videoEl) {
        const existing = document.getElementById('__cam-preview');
        if (existing) existing.remove();

        const wrap = document.createElement('div');
        wrap.id = '__cam-preview';
        Object.assign(wrap.style, {
            position: 'fixed', bottom: '80px', right: '16px',
            width: '160px', height: '90px',
            borderRadius: '8px', overflow: 'hidden',
            border: '2px solid #a0c4ff',
            boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
            zIndex: '9999', cursor: 'move',
            background: '#000',
        });

        const preview = document.createElement('video');
        preview.srcObject = videoEl.srcObject;
        preview.autoplay = true;
        preview.muted = true;
        preview.playsInline = true;
        Object.assign(preview.style, { width: '100%', height: '100%', objectFit: 'cover' });
        wrap.appendChild(preview);

        // 拖拽支持
        let dragging = false, ox = 0, oy = 0;
        wrap.addEventListener('mousedown', e => {
            dragging = true; ox = e.clientX - wrap.getBoundingClientRect().left;
            oy = e.clientY - wrap.getBoundingClientRect().top;
        });
        document.addEventListener('mousemove', e => {
            if (!dragging) return;
            wrap.style.left = (e.clientX - ox) + 'px';
            wrap.style.top = (e.clientY - oy) + 'px';
            wrap.style.right = 'auto'; wrap.style.bottom = 'auto';
        });
        document.addEventListener('mouseup', () => { dragging = false; });

        document.body.appendChild(wrap);
    }

    /**
     * 启动视觉调试面板（左下角，显示最新描述）
     */
    function _startVisionDebugPanel() {
        const existing = document.getElementById('__vision-debug');
        if (existing) existing.remove();

        const panel = document.createElement('div');
        panel.id = '__vision-debug';
        Object.assign(panel.style, {
            position: 'fixed', bottom: '16px', left: '16px',
            width: '280px', maxHeight: '200px',
            background: 'rgba(10,10,20,0.92)',
            border: '1px solid #333', borderRadius: '8px',
            padding: '8px 10px', zIndex: '9998',
            fontSize: '11px', color: '#a0c4ff',
            overflowY: 'auto', lineHeight: '1.5',
            fontFamily: 'monospace',
        });
        panel.innerHTML = '<b style="color:#fff">📷 视觉调试</b><br>等待数据...';
        document.body.appendChild(panel);

        // 每 2 秒刷新一次
        const timer = setInterval(async () => {
            if (!isRecording) { clearInterval(timer); return; }
            try {
                const data = await requestJson('/api/web/video/context');
                const list = data?.vision_list ?? [];
                if (!list.length) return;
                const recent = list.slice(-5).reverse();
                panel.innerHTML = '<b style="color:#fff">📷 视觉调试</b> (' + list.length + ' 帧)<br>' +
                    recent.map((f, i) => {
                        const t = new Date(f.timestamp * 1000).toLocaleTimeString();
                        return `<span style="color:#666">${t}</span> <span style="color:#e0e0e0">${f.image_description}</span>`;
                    }).join('<br>');
            } catch {}
        }, 2000);
    }

    /**
     * 连接 WebSocket
     */
    function _connectWebSocket() {
        return new Promise((resolve, reject) => {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${location.host}/api/web/video/stream`;
            console.log('Connecting WebSocket:', url);

            websocket = new WebSocket(url);

            // 超时保护：5 秒内未连接则失败
            const timer = setTimeout(() => {
                reject(new Error('WebSocket 连接超时'));
                websocket.close();
            }, 5000);

            websocket.onopen = () => {
                clearTimeout(timer);
                console.log('WebSocket connected');
                resolve();
            };
            websocket.onerror = (e) => {
                clearTimeout(timer);
                console.error('WebSocket error:', e);
                reject(new Error('WebSocket 连接失败'));
            };
            websocket.onclose = () => {
                websocket = null;
            };
        });
    }

    /**
     * 采集视频帧（每秒 1 帧）
     */
    function _captureFrames(videoEl) {
        const tick = () => {
            if (!isRecording) return;
            videoCtx.drawImage(videoEl, 0, 0, videoCanvas.width, videoCanvas.height);
            videoCanvas.toBlob((blob) => {
                if (websocket && websocket.readyState === WebSocket.OPEN) {
                    websocket.send(blob);
                }
            }, 'image/jpeg', 0.8);
            setTimeout(tick, 1000);
        };
        tick();
    }

    /**
     * 更新按钮状态
     */
    function _updateVideoUI(active) {
        const startBtn = document.getElementById('start-video-call-btn');
        const stopBtn  = document.getElementById('stop-video-call-btn');
        const status   = document.getElementById('video-call-status');
        if (startBtn) startBtn.hidden = active;
        if (stopBtn)  stopBtn.hidden  = !active;
        if (status)   status.textContent = active ? '采集中…' : '';
    }

    /**
     * 获取视觉上下文列表（供 chat.js 在发消息时调用）
     * 返回后端维护的滑动窗口数据
     */
    async function getVisionContext() {
        try {
            const data = await requestJson('/api/web/video/context');
            return data?.vision_list ?? [];
        } catch {
            return [];
        }
    }

    return { initialize };
}
