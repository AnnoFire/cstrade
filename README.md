# CS2 挂刀行情（本机 · 阶段 1 · 免登录核心）

核心工作流：搜索 CS2 饰品 → 看 BUFF 实时最低卖价、在售量、挂单源时间戳 →（配 Steam 代理后）
按"扣 Steam 手续费后的余额比例"排序 → 跳转回平台核对。前端**每 30~120 秒自动刷新**。

## 免登录，无任何账号/cookie

参考原站 EricZhu-42/SteamTradingSiteTracker 的实现后，本工具用"公开数据集 + 免登录只读接口"
重搭，**不需要任何 BUFF 登录 cookie**：

- **全目录检索**：用原站公开的 **ID-Mapper 数据集**（MIT，`market_hash_name → BUFF goods_id / 中文名 / Steam name_id / UU id`，CS2 覆盖约 99.9%）。首次启动自动从 GitHub 下载约 10MB 到 `data/idmap/`。
- **BUFF 实时最低价 / 在售量 / 挂单源时间戳**：走免登录的 `sell_order` 接口，按 goods_id 实时取，短 TTL 缓存。
- **余额比例的分母（Steam 价）**：Steam 在中国大陆被墙、本机直连 `HTTP 000`。取数优先级：
  1) 有海外出口（本地代理或境外 runner）→ 直连官方 `priceoverview`（币种 23=CNY）取**国服实时最低卖单价**，比例**精确**；
  2) 否则回落公开聚合快照（csgotrader，全球区/美元）取**中位成交价**估算，界面标"全球估"，**系统性偏高、仅供排序**。

> 一个市场、多种"参考价"：BUFF 价与 Steam 价本就不同（这正是挂刀利润）；而聚合中位价又高于实时最低卖单价，
> 所以"全球估"比例会虚高。**真实收益只以你账号所在地区、当时的可成交价为准**，交易前回平台核对。

## 与纯前端的区别

BUFF/Steam 接口不发 CORS 头，浏览器跨域读不到，所以纯静态网页做不到实时。本项目的"后端"只是
一个**只绑 `127.0.0.1` 的本机进程**（专用于绕过 CORS 取数），不部署、不联网暴露、无写操作、无凭据。
体验即"打开浏览器 → 页面自动实时刷新"。

## 运行

```bash
python3 server.py
# 浏览器打开 http://127.0.0.1:8756
```

仅标准库，无需 pip。服务只绑 `127.0.0.1`。

## 部署到线上（GitHub Pages + Actions，免费、无服务器）

纯浏览器直连 BUFF/Steam 会被 CORS 拦死，所以线上用"定时预计算 → 静态站"模型（同原站思路，只是把中央服务换成免费的 GitHub Actions）。`.github/workflows/deploy.yml` 每约 10 分钟：

```
export.py 生成 site/data.json + 复制前端  →  发布 site/ 到 GitHub Pages
浏览器(纯静态,读 data.json)  ——  无 /api，自动进入静态模式、客户端搜索
```

- 前端**双模式自适应**：探测到 `/api` 就走本地实时；Pages 上 404 则读 `data.json`（条件请求，避免重复下载 10MB）。
- 数据分两档：`关注列表(hotlist.txt)` 的件在**境外 runner** 上直连 Steam → 标"精确"；其余全量走聚合 → 标"全球估"。
- runner 在境外，正好解决 Steam 被墙（本机测不出，部署后生效）。

启用步骤：
1. 建一个 GitHub 仓库，把本目录内容推上去（`data/`、`site/` 已被 `.gitignore` 忽略，不上传）。
2. 仓库 Settings → Pages → Source 选 **GitHub Actions**。
3. 编辑根目录 `hotlist.txt`（每行一个 `market_hash_name`）设为你的关注列表 → 推送触发首次构建。
4. 自定义域名：仓库设置里填域名，或构建时设 `SITE_DOMAIN` 变量生成 `CNAME`，再在 DNS 加 CNAME 记录指向 `<user>.github.io`。

> 局限：全量价格时效受上游聚合(约每小时)限制；关注列表实时但件数要少（GitHub Actions 对高频抓取有限流）。免费方案刷新下限约 5 分钟，做不到全量分钟级。

## 配置（均可选，坏值启动即 fail fast）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CSTRADE_PORT` | 8756 | 本机端口 |
| `CSTRADE_STEAM_PROXY` | 空 | 海外 HTTP 代理 `host:port`，仅用于访问 Steam；不填则比例缺省 |
| `STEAM_CURRENCY` | 23 | Steam 币种，23=CNY |
| `SEARCH_LIMIT` | 20 | 单次搜索水合并返回的条数（控上游访问强度） |
| `BUFF_PRICE_TTL` | 60 | BUFF 实时价缓存秒数 |
| `STEAM_PRICE_TTL` | 600 | Steam 价缓存秒数 |
| `CSTRADE_IDMAP_DIR` | `data/idmap` | ID-Mapper 数据集本地缓存目录 |
| `CSTRADE_DB` | `data/catalog.sqlite3` | 本地缓存（只存公开行情/映射，无凭据） |

### 海外代理怎么来（只为 Steam 价，三选一）

1. **海外 VPS + SSH 隧道**（最省）：`ssh -N -D 127.0.0.1:13389 you@vps` 得到 SOCKS5；
   标准库只认 HTTP 代理，故二选一：① VPS 上装 Privoxy/Squid 转成 HTTP 后填 `CSTRADE_STEAM_PROXY=127.0.0.1:8118`；
   ② 或 `pip install pysocks` 并把 `steam.py` 的 opener 换成支持 socks（需少量改动）。
2. **买住宅/机房 HTTP 代理**：拿到 `ip:port`（或 `user:pass@host:port`）直接填 `CSTRADE_STEAM_PROXY`。
3. **把服务直接跑在海外机器**：`python3 server.py` 在 VPS 上运行，浏览器访问，则无需单独代理（注意自行做端口/访问保护）。

## 余额比例口径

```text
预计 Steam 净入账 = 国服Steam参考价 − Steam手续费(5%) − 游戏手续费(10%)
余额比例 = 预计净入账 / BUFF最低购买价 × 100%   （越高越划算）
```

手续费为**移植原站的逐分反解**（`calculate_after_fee`：买家价 = 到手 + floor(max(5%,1分)) + floor(max(10%,1分))，迭代精确求到手），非写死 15%。

## 安全红线（方案 §6）

- 全程无密码/Cookie/令牌；`.gitignore` 忽略 `data/`。
- 服务只读、只绑回环；错误日志只打方法/路径/状态码，绝不输出任何请求头。
- 出现限频(429)自动退避；命中异常即停止该平台取数并如实提示，不绕过风控。
- 清除本机数据：删除 `data/` 即可。

## 阶段边界

- 含：CS2、BUFF 实时报价/在售量/挂单时间戳、国服 Steam 参考价（需代理）、手续费后比例排序、页面自动刷新。
- 不含（本阶段）：UU 实时报价（ID 已在库，实时价需 `Bearer`，未接）、Dota2、自动下单、历史图表、云端部署、任何上传凭据的行为。
