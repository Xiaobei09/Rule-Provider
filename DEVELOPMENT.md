# 开发规范与生成细节约束

本文档是本项目的权威约束说明。所有生成脚本、CI 流程、人工贡献都必须遵守。
对本文档的修改应通过 PR 进行，并同步更新 README.md 与 `config/config.yaml`。

---

## 1. 项目目标

参考 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)
的组织风格，以「权威数据源 + 每日自动生成 + 确定性输出」的方式，提供：

- 全球各国/地区的 IP 规则集（geoip，Rule Provider）；
- 全球各国/地区的**域名规则集（site，Rule Provider）**；
- 覆盖全球全部地址段 / 全部国家域名的 Global Rule Provider；
- 供 Surge / Clash / QuantumultX / Loon 使用。

**原则：我们生产规则，不搬运规则。** 所有数据均来自公开权威数据源，由本仓库脚本
自动生成，保证可追溯、可复现。

---

## 2. 数据源

### 2.1 首选：五大 RIR delegated 扩展文件

数据源为 IANA 授权的五个区域互联网注册机构（RIR）发布的权威分配数据：

| RIR | 覆盖区域 | URL |
| --- | --- | --- |
| AFRINIC | 非洲 | `https://ftp.afrinic.net/stats/afrinic/delegated-afrinic-extended-latest` |
| APNIC | 亚太 | `https://ftp.apnic.net/stats/apnic/delegated-apnic-extended-latest` |
| ARIN | 北美 | `https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest` |
| LACNIC | 拉美 | `https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest` |
| RIPE NCC | 欧洲/中东 | `https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest` |

> 注意：各 RIR 的文件**只包含本区域**数据，全球覆盖必须下载全部五个后合并。
> 历史版本的 `delegated-apnic-latest` 曾包含合并数据，现已不再提供，禁止回退使用。

### 2.2 可选补充：MaxMind GeoLite2

- 真实地理归属（如托管机房、CDN 归属）比 RIR 分配更精细；
- 需要免费 License Key，通过环境变量 `MAXMIND_LICENSE_KEY` 提供；
- 默认关闭；开启后作为 RIR 数据的**补充**（并集）。

### 2.3 site（域名）数据源：v2fly/domain-list-community

用于生成各国域名规则集（geosite）。数据源为互联网公开仓库
[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community)
（MIT 许可），下载其 master 分支的 tar.gz 到 `cache/geosite-v2fly.tar.gz`。

关键数据文件：

| 文件 | 内容 |
| --- | --- |
| `data/tld-!cn` | 全球 ccTLD 清单（`tld # 国家名`，约 780 行），仅排除中国相关 TLD |
| `data/tld-cn` / `data/tld-ru` | 中国 / 俄罗斯 TLD（含公司新 gTLD 与 IDN） |
| `data/<category>` | geosite 分类（如 `cn` = `tld-cn` + `geolocation-cn`） |

**tld-!cn 解析为国家映射的规则：**

1. 行格式 `tld # Country Name`，国家名需归一化（去重音、撇号/逗号归空格、大写）
   后匹配 ISO 3166-1 alpha-2 英文名；内置别名表覆盖 `Republic of Korea`、
   `Macau`、`United Kingdom of Great Britain and Northern Ireland`、
   `Côte d’Ivoire` 等拼写差异；
2. 注释尾部括号内 2-3 字母代码仅当其恰为名称单词首字母缩写时才作为国家码
   （如 `United States of America (USA)` → US、`United Kingdom (UK)` → GB），
   防止把公司名中的 `(HK)` 误判为香港（`Pacific Century Asset Management (HK) Limited`）；
3. v2fly 缺失、但 IANA 已委派且在使用的 ccTLD，由 `geosite.FALLBACK_CCTLD`
   手工补全（来源：`https://data.iana.org/TLD/tlds-alpha-by-domain.txt`，
   含 `hk/kp/co/fo/fm/gq/me/mk/pw/re/sz/tt/tv/tf/cc`）；
4. `include:` 行（如 `include:tld-ru`）不参与国家映射。

**分类文件解析规则：**

1. 仅保留四类规则：裸域名/`domain:`（后缀匹配）、`full:`（精确）、`keyword:`、
   `regexp:`；行内 `#` 注释与 `@属性` 一律剥离；
2. `include:<name>` 递归展开（循环引用以 seen 集合防护）；
3. 跳过 `attribute:`、`ext:`、`country:` 等非规则行；
4. 每个国家规则 = 该国 ccTLD 的 domain 规则 + 配置的 `categories` 分类
   （默认 `CN: cn`、`RU: tld-ru`），去重后按 `(类型, 值)` 稳定排序；
5. `Global` site = 全部国家域名规则并集。

### 2.5 site 补充来源：各国热门网站（CrUX top sites）

为丰富各国「真实热门网站」域名，可选启用 `sources.top_sites`
（Chrome CrUX 按国家 Top 网站公开数据，
[InternetHealthReport/crux-top-lists-country](https://github.com/InternetHealthReport/crux-top-lists-country)，
数据来自 Google Chrome UX Report，每月更新）。默认启用，每国取 `top_n: 5000`。

- 每国数据为 gzip CSV：`origin,rank`，`rank` 是量级桶（1000/10000/100000/1000000），
  行按桶有序、桶内随机；`origin` 为完整 URL（如 `https://www.example.com`）；
- **下载策略**：rank≤10000 的行位于文件头部，故仅用 HTTP Range 请求文件前若干 KB
  即可覆盖 top_n 所需数据（`topsites._fetch_head`，256KB 起步、不足逐级放大），
  避免整文件（约 8MB × 238 国）重复下载；gzip 流截断时只解压已收到部分；
- **最新月份探测**：一次 Git Tree API 调用
  （`git/trees/main?recursive=1`）解析全部 `data/country/<cc>/<YYYYMM>.csv.gz`，
  取每国最大月份，结果随缓存保留；
- **域名规范化**：用 Public Suffix List
  （`https://publicsuffix.org/list/public_suffix_list.dat`，ICANN + PRIVATE 两段）
  计算可注册域名（eTLD+1），如 `www.foo.co.uk → foo.co.uk`、
  `a.blogspot.com → a.blogspot.com`；无法确定（IP/裸公后缀/未知后缀）的丢弃；
- **归属过滤**：`rank=1000` 的行优先，随后取 `rank=10000` 的行，去重后截取
  top_n；被该国 ccTLD 后缀规则已覆盖的域名（如中国列表中的 `*.cn`）自动剔除，
  保留「在该国热门但非本国 ccTLD」的站点；
- 结果缓存为 `cache/crux-top-sites.json`（不入库）；与 ccTLD、`categories` 规则
  合并后一并进入 `ruleset/site/`（`Global` site 同样包含其并集）。

### 2.6 geoip 补充来源：IPtoASN

为弥补 RIR「分配国」与「实际运营国」的错位，可选启用 `sources.ip2asn`
（[IPtoASN](https://iptoasn.com/)，免费 IP -> ASN/国家数据库，
Public Domain / PDDL v1.0，每小时更新，无需任何密钥）。默认启用。

- 文件为 gzip TSV（无表头）：`range_start range_end AS_number country_code AS_description`，
  IPv4 / IPv6 各一个文件（约 7MB + 2MB）；
- `AS_number == 0` 或 `country_code == None`（`Not routed`）的行一律跳过；
- 每行闭区间 `[start, end]` 拆分为最少个规范 CIDR（允许非 2 的幂），
  IPv4 与 IPv6 使用同一拆分算法（`iptoasn.split_range_to_cidrs`）；
- 解析结果为 `{CC: [networks]}`，与 RIR 数据做**并集**合并后进入
  `ruleset/geoip/` 与 `Global`；跨国段将同时出现在「分配国」与「ASN 运营国」两国；
- 文件无内嵌版本日期：数据日期取 HTTP `Last-Modified` 头（GMT），
  保存在 `cache/ip2asn-date.txt`，`metadata.json` 的 `generated_at` 取
  RIR 与 IPtoASN 中日期的较大者，保证同日重复生成输出一致（幂等）。

### 2.4 数据源约束

1. 只接受 HTTP 状态 200 的内容；下载必须带 UA，使用自适应分块下载（避免大文件被
   代理/网络截断，参见 `sources._download`）。
2. 解析规则：
   - 行格式：`registry|cc|type|start|value|date|status`；
   - `type` 仅保留 `ipv4` / `ipv6`，忽略 `asn`；
   - `status` 仅保留配置允许的集合（默认 `allocated` + `assigned`）；
   - 国家码 `cc` 非空、转大写；空国家码、`available`/`reserved` 等状态一律丢弃；
   - IPv4 的 `value` 是地址数量（可能非 2 的幂），必须拆分为规范的 CIDR；
   - IPv6 的 `value` 直接作为前缀长度。
3. 下载数据只允许缓存到 `cache/`（已加入 `.gitignore`），**禁止提交原始数据**。

---

## 3. 生成细节约束

### 3.1 CIDR 处理

- 所有条目必须是**规范 CIDR** 表示，IPv4 无前导零、IPv6 按 RFC 5952 压缩；
- 同一国家内先合并：`merge_networks` 只允许相邻或包含关系合并，禁止改变地址归属；
- 合并必须跨 IPv4/IPv6 分版本进行，不同版本不得互相合并或比较。

### 3.2 保留段过滤

输出前必须剔除 IANA 特殊用途地址段（`cidr.RESERVED_IPV4` / `RESERVED_IPV6`），
包括但不限于：RFC1918 私网、回环、链路本地、多播、TEST-NET、文档示例段、
运营商级 NAT、6to4 等。列表更新时同步修改 `cidr.py` 并补充单元测试。

### 3.3 排序与确定性（幂等性）

- 每个文件内的条目必须稳定排序（按版本、按 CIDR 字符串排序）；
- 重复运行生成脚本，输出必须逐字节一致（CI 中有幂等性检查步骤）；
- 时间戳使用**数据日期**（RIR 文件头部版本行日期），而非实时运行时间，
  保证同日数据下任意次运行输出一致；数据变化或跨日后输出才会变化；
- `metadata.json` 中的 `generated_at` 即数据日期，内容项必须稳定；
- 国家顺序在 `metadata.json` 中按代码升序。

### 3.4 写入方式

- 生成文件必须「先写临时文件再原子重命名」（`render.write_rule_set`），
  防止进程中断产生半截文件；
- 编码统一 UTF-8，行尾 `\n`，文件末尾必须有换行；
- 输出目录中不属于本次生成集合的文件必须清理（防止历史遗留污染）。

### 3.5 文件命名与目录

```
ruleset/
├── metadata.json              # geoip 全量元数据（国家名/统计/生成时间）
├── geoip/
│   ├── Surge/CN.list          # 按国家代码 + 格式后缀命名
│   ├── Clash/CN.yaml
│   ├── QuantumultX/CN.txt
│   └── Loon/CN.list
├── global/
│   ├── Surge/Global.list      # 全球并集，固定命名为 Global
│   ├── Clash/Global.yaml
│   ├── QuantumultX/Global.txt
│   └── Loon/Global.list
└── site/                      # 域名规则集（geosite：ccTLD + 分类 + 可选 top sites）
    ├── metadata.json          # site 元数据
    ├── Surge/CN.list          # DOMAIN-SUFFIX,...
    ├── Clash/CN.yaml
    ├── QuantumultX/CN.txt     # host-suffix, ...
    ├── Loon/CN.list
    ├── Surge/Global.list      # 全球域名并集
    └── ...
```

- 国家代码使用 ISO 3166-1 alpha-2 大写；特殊代码：`EU`/`AP`/`XK`/`ZZ` 等；
- 无数据的国家不生成文件（如无人岛领地）；`site` 目录同理，仅含 ccTLD
  或配置了分类的国家。

---

## 4. 输出格式规范

所有格式的头注释必须包含：国家/地区、生成时间、数据来源、规则统计。
行尾策略（`no-resolve`）由配置 `rules.no_resolve` 控制。

| 格式 | 扩展名 | IPv4 行 | IPv6 行 | 说明 |
| --- | --- | --- | --- | --- |
| Surge | `.list` | `IP-CIDR,1.0.0.0/24,no-resolve` | `IP-CIDR6,2001::/32,no-resolve` | Surge 模块引用 |
| Clash | `.yaml` | `  - IP-CIDR,1.0.0.0/24,no-resolve` | `  - IP-CIDR6,2001::/32,no-resolve` | `payload:` 列表 |
| QuantumultX | `.txt` | `ip-cidr, 1.0.0.0/24` | `ip6-cidr, 2001::/32` | QX 过滤器语法 |
| Loon | `.list` | `IP-CIDR,1.0.0.0/24,no-resolve` | `IP-CIDR6,2001::/32,no-resolve` | 与 Surge 语法兼容 |

约束：
1. 禁止输出原始整数、通配符、非规范 CIDR；
2. QuantumultX 不输出 `no-resolve`（语法不支持）；
3. Clash YAML 首行必须是 `payload:`，每个条目缩进两个空格并以 `- ` 开头；
4. 注释行以 `#` 开头，不得污染规则解析。

### 4.1 site（域名）格式规范

四种规则类型与各格式的对应行（值需为小写域名 / 关键字 / 正则）：

| 规则类型 | Surge / Loon | Clash | QuantumultX |
| --- | --- | --- | --- |
| 后缀匹配（`domain:`/裸域名） | `DOMAIN-SUFFIX,example.com` | `  - DOMAIN-SUFFIX,example.com` | `host-suffix, example.com` |
| 精确（`full:`） | `DOMAIN,example.com` | `  - DOMAIN,example.com` | `host, example.com` |
| 关键字（`keyword:`） | `DOMAIN-KEYWORD,foo` | `  - DOMAIN-KEYWORD,foo` | `host-keyword, foo` |
| 正则（`regexp:`） | `DOMAIN-REGEX,...` | `  - DOMAIN-REGEX,...` | `host-regex, ...` |

约束：
1. Clash 的 site 规则集须在配置中声明 `behavior: classical`（非 `ipcidr`）；
2. QuantumultX 前缀小写、逗号后带一个空格，与 geoip 规则一致；
3. 头注释统计按类型分列（域名/精确域名/关键字/正则）；
4. 排序：先按类型（domain < full < keyword < regexp），再按值字典序；
   重复的 `(类型, 值)` 只保留一条。

---

## 5. 配置项

见 `config/config.yaml`。修改配置后：

1. 运行 `python3 scripts/generate.py generate` 确认生成成功；
2. 运行 `python3 scripts/generate.py validate` 确认无错误；
3. 同步更新本文档与 README 中涉及默认值的描述。

---

## 6. 开发规范

### 6.1 语言与运行时

- Python 3.11+，**运行时代码零第三方依赖**（仅标准库），保证 CI/本地一致；
- 全部代码文件必须有模块级 docstring 与类型注解。

### 6.2 代码风格

- 遵循 PEP 8，行长 ≤ 100；
- 字符串一律单引号，除非字符串内含单引号；
- 命名：函数/变量 `snake_case`，常量 `UPPER_SNAKE`，类 `PascalCase`；
- 禁止冗余注释，关键约束必须注释并指向本文档对应章节。

### 6.3 测试

- 测试文件位于 `tests/test_*.py`，覆盖：CIDR 合并/过滤、RIR 解析、各格式渲染、
  geosite 分类解析 / ccTLD 国家映射 / site 渲染、配置解析、metadata 结构、
  top sites（PSL 解析 / eTLD+1 / CrUX 解析 / gzip 截断解压）；
- 每个修复/新功能必须附带或更新对应测试；
- 本地运行：`python3 run_tests.py`（零依赖）或 `python3 -m pytest tests/`；
- CI 强制运行全部测试。

### 6.4 提交信息规范（Conventional Commits）

```
chore: daily update country rule sets (2026-08-14)      # 自动化每日提交
feat: add IPv6-only filter option                        # 新功能
fix: handle non-power-of-two IPv4 allocations            # 缺陷修复
docs: clarify reserved range policy in DEVELOPMENT.md    # 文档
test: add merge_networks adjacency cases                 # 测试
```

### 6.5 分支与 PR

- 主分支为 `main`；
- 人工改动请基于 `main` 建分支，提交后发 PR；
- 合入前必须通过 CI（单元测试 + 生成校验 + 幂等性检查）；
- 生成的 `ruleset/` 由每日工作流自动提交，人工 PR 一般不应包含 `ruleset/` 改动。

### 6.6 本地开发流程（固定命令）

所有常规开发操作都通过 `Makefile` 固定，命令与 CI 各步骤一一对应：

| 命令 | 等价步骤 | 是否需要联网 |
| --- | --- | --- |
| `make test` | `python3 run_tests.py`（全部单元测试） | 否 |
| `make fetch` | `python3 scripts/generate.py fetch` | 是 |
| `make generate` | `python3 scripts/generate.py generate` | 否 |
| `make validate` | `python3 scripts/generate.py validate` | 否 |
| `make check` | generate + validate + 重复 generate 做幂等性 diff | 否 |
| `make all` | fetch + check（与每日更新等价） | 是 |
| `make clean` | 删除 `ruleset/` 生成产物 | 否 |

开发流程定式：

1. 修改代码/配置后：`make test` 通过；
2. 涉及数据源时：`make fetch`（联网）→ `make check`；
3. 提交前自查：`make check` 与 `make test` 全绿；
4. 提交信息遵循 §6.4 Conventional Commits，推送后发 PR，CI 复核。

### 6.7 CI 工作流

- `.github/workflows/ci.yml`：push/PR 触发，跑测试、语法检查、生成校验、幂等性；
- `.github/workflows/daily-update.yml`：每日 04:30 UTC 触发，fetch + generate +
  validate + 有变化才 commit & push（使用 `github-actions[bot]`）。

### 6.8 发布与致谢

- 发布规则集时在 README 更新数据快照信息；
- 数据来源致谢：AFRINIC / APNIC / ARIN / LACNIC / RIPE NCC / MaxMind /
  IPtoASN / v2fly（domain-list-community）。
