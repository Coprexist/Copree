"""协议端控制台的反代适配 —— 平台只转发，NapCat 的路径口径归这里

平台（app/routers/channel_panel.py）对控制台一无所知：它发一张一次性票据、按插件给的地址
转发、把文本交给插件改写。反过来，"控制台挂在 webui/ 下"、"WebUI 前端把 /webui/ 和 /api
写成绝对路径、挂到子路径下会打到站点根"——这些是 NapCat WebUI 的事实，所以留在这个文件。

两个钩子由插件实例转发（见 plugin.py 的 panel_entry / panel_rewrite）：
- entry(ticket)：票据落地路径，相对平台反代基路径；
- rewrite(text, base)：把文本（HTML/CSS）里的绝对路径补上基路径，HTML 再塞一段运行时 shim。
"""
from __future__ import annotations

# 静态资源由反代改写；/api 是运行时拼的，改不了字符串，所以注入一小段 shim 在浏览器侧统一
# 加前缀 —— 版本升级换了 chunk 文件名也不用跟着改。
_SHIM = """<script>(function(){var B="__BASE__";function f(u){if(typeof u!=="string"||u.charAt(0)!=="/")return u;if(u.indexOf(B)===0)return u;return B+u}
var of=window.fetch;if(of){window.fetch=function(u,o){return of.call(this,f(u),o)}}
var oo=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(){var a=[].slice.call(arguments);if(a.length>1)a[1]=f(a[1]);return oo.apply(this,a)};
var E=window.EventSource;if(E){var S=function(u,o){return new E(f(u),o)};S.prototype=E.prototype;window.EventSource=S}})()</script>"""


def entry(ticket: str) -> str:
    """控制台入口：NapCat 的 WebUI 在 webui/ 下，票据挂在查询串上"""
    return f"webui/?t={ticket}"


def rewrite(text: str, base: str) -> str:
    """绝对路径补上反代基路径；HTML 再塞一段运行时 shim"""
    text = text.replace('"/webui/', f'"{base}/webui/').replace("'/webui/", f"'{base}/webui/")
    text = text.replace("url(/webui/", f"url({base}/webui/")
    if "</head>" in text:
        text = text.replace("</head>", _SHIM.replace("__BASE__", base) + "</head>", 1)
    return text
