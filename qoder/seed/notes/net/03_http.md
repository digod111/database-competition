---
course: 计算机网络
chapter: 应用层
title: HTTP 缓存与状态码
keywords: HTTP, 缓存, Cache-Control, ETag, 304, 状态码, 持久连接, keep-alive
knowledge_points: HTTP 缓存机制, HTTP 状态码, HTTP 持久连接
---
## HTTP 缓存机制

HTTP 缓存分为强缓存和协商缓存。

- 强缓存：浏览器直接使用本地副本，不向服务器发请求。`Cache-Control: max-age=3600` 表示 3600 秒内资源新鲜；`Expires` 是旧式绝对时间字段，受客户端时钟影响。
- 协商缓存：缓存过期后，浏览器携带验证字段向服务器确认资源是否变化。`If-None-Match` 对应 `ETag`，`If-Modified-Since` 对应 `Last-Modified`。
- 服务器判断资源未变化时返回 304 Not Modified，不返回响应体，浏览器继续使用本地副本。
- `no-cache` 不是不缓存，而是每次使用前必须向服务器验证；`no-store` 才是不存储任何缓存。
- ETag 比 Last-Modified 更精确，能识别同一秒内的修改和内容变化。

## HTTP 状态码

- 1xx：信息性响应，例如 101 Switching Protocols。
- 2xx：成功，例如 200 OK、204 No Content。
- 3xx：重定向或缓存验证，例如 301 永久重定向、302 临时重定向、304 Not Modified。
- 4xx：客户端错误，例如 400 请求错误、401 未认证、403 禁止访问、404 未找到。
- 5xx：服务端错误，例如 500 内部错误、502 网关错误、504 网关超时。

301 和 302 的关键区别在于是否永久；304 只用于条件请求命中缓存，不携带响应体。

## HTTP 持久连接

HTTP/1.0 默认每个请求建立一个 TCP 连接，HTTP/1.1 默认使用持久连接（keep-alive），多个请求可以复用同一个 TCP 连接。

- 减少 TCP 握手和慢启动开销，提高页面加载效率。
- HTTP/1.1 的管线化仍可能受到队头阻塞影响；HTTP/2 通过多路复用缓解。
- 服务端和客户端都可以通过 `Connection: close` 主动关闭连接。
- 持久连接需要合理设置超时，避免空闲连接长期占用资源。
