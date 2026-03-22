package com.echobot.app;

import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebView;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    private static final int CAMERA_PERMISSION_REQUEST = 1001;
    private PermissionRequest pendingPermissionRequest;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
    }

    @Override
    public void onResume() {
        super.onResume();
        setupWebViewCamera();
    }

    private void setupWebViewCamera() {
        WebView webView = getBridge().getWebView();
        webView.getSettings().setMediaPlaybackRequiresUserGesture(false);
        webView.getSettings().setJavaScriptEnabled(true);
        // 允许非安全上下文（http://）使用 getUserMedia
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            webView.getSettings().setMixedContentMode(
                android.webkit.WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        // 注入脚本：在页面加载前覆盖安全检查
        webView.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public boolean isAndroid() { return true; }
        }, "AndroidBridge");
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> {
                    String[] requestedResources = request.getResources();
                    boolean needCamera = false;
                    for (String r : requestedResources) {
                        if (r.equals(PermissionRequest.RESOURCE_VIDEO_CAPTURE)
                                || r.equals(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) {
                            needCamera = true;
                            break;
                        }
                    }
                    if (needCamera) {
                        if (ContextCompat.checkSelfPermission(
                                MainActivity.this, Manifest.permission.CAMERA)
                                == PackageManager.PERMISSION_GRANTED) {
                            request.grant(request.getResources());
                        } else {
                            pendingPermissionRequest = request;
                            ActivityCompat.requestPermissions(
                                    MainActivity.this,
                                    new String[]{
                                        Manifest.permission.CAMERA,
                                        Manifest.permission.RECORD_AUDIO
                                    },
                                    CAMERA_PERMISSION_REQUEST
                            );
                        }
                    } else {
                        request.grant(request.getResources());
                    }
                });
            }
        });
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_PERMISSION_REQUEST && pendingPermissionRequest != null) {
            if (grantResults.length > 0
                    && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                pendingPermissionRequest.grant(pendingPermissionRequest.getResources());
            } else {
                pendingPermissionRequest.deny();
            }
            pendingPermissionRequest = null;
        }
    }
}
