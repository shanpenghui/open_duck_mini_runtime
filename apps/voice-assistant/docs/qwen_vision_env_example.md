# Qwen-VL .env 配置示例

这份文档只放配置模板，不放真实 API key。

## 推荐位置

在树莓派本机用户目录下创建：

```text
~/.openduck/.env
```

`~` 的意思是“当前登录用户的家目录”。比如你用 `duck` 用户登录树莓派，`~/.openduck/.env` 通常就是：

```text
/home/duck/.openduck/.env
```

## 示例内容

```text
QWEN_VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_VISION_MODEL=qwen-vl-plus
QWEN_VISION_API_KEY=不要把真实 key 写进文档或发给别人
```

真实使用时，只把最后一行等号后面换成你自己的真实 key。

## 为什么不要放在项目目录里

项目目录里的文件容易被打包、同步、截图、提交 Git，API key 一旦泄露，别人就可能拿你的额度去调用模型。

所以真实 key 只能放在树莓派本机用户目录下，比如：

```text
~/.openduck/.env
```

不要做这些事：

- 不要把真实 key 写进代码。
- 不要把真实 key 写进文档。
- 不要把真实 key 放在项目代码目录里。
- 不要把带 key 的终端、编辑器、配置文件截图发给别人。
- 不要提交 Git。

## 工具会读取哪些配置

`server/qwen_vision_tool.py` 会按下面方式读取：

```text
QWEN_VISION_BASE_URL
QWEN_VISION_MODEL
QWEN_VISION_API_KEY
```

读取来源包括：

```text
环境变量
~/.openduck/.env
~/.openclaw/.env
```

如果没有配置 API key，工具会返回 JSON：

```json
{
  "ok": false,
  "error": "missing_api_key",
  "message": "Qwen vision API key is not configured."
}
```

这不是程序崩溃，而是安全失败：说明工具发现没有 key，所以没有继续请求云端模型。
