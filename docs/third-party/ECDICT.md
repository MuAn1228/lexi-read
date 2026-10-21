# ECDICT 离线英汉词典

词间捆绑的是 [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT) 的基础 CSV 转换版本，使用上游 **MIT License**，版权为 **Copyright (c) 2025 Linwei**。完整授权文本随项目保存于 [ECDICT-LICENSE.txt](ECDICT-LICENSE.txt)，分发应用和词典资产时应一并保留。

## 固定来源

- 上游提交：`bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b`
- 原始文件：[ecdict.csv](https://raw.githubusercontent.com/skywind3000/ECDICT/bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b/ecdict.csv)
- 源 CSV SHA-256：`1a6947e04785db63613a92e14903cdae7954f7e84860b10e68e5c7cbb3f9c3cf`
- 源记录：770,611 条；保留 757,041 个词条，排除 13,570 条不符合当前英文拼写规则或缺少中文内容的记录。
- `backend/data_assets/dictionary/manifest.json` 记录来源、版本、条数、压缩文件与数据库的 SHA-256 和字节数。

## 转换规则

构建脚本 `scripts/build_dictionary.py` 只读取公开源 CSV，不读取应用配置、墨墨账户或用户词库。

1. 保留最长 150 字符的英文单词及短语，拼写规则与项目一致：字母之间可出现单个空格、连字符或撇号。统一大小写、弯撇号和短横线作为索引；展示保留上游词头。**不对词头做词干合并**。
2. 保留所有非空中文义项、英文义项、音标和 `exchange` 词形数据；将源文件中的字面 `\n` 转为真正换行，去除完全重复的义项，不截断完整释义。
3. 若出现大小写归一后相同的词头，合并独立中英文义项和词形，以首次出现的词头作为展示名。本固定版本没有发生这类合并。
4. 使用 `WITHOUT ROWID` SQLite 主键索引保存词条；不保留词频、考试标签和空扩展字段。按源 CSV 顺序写入后 `VACUUM`。
5. 使用 gzip 级别 9，固定 `mtime=0`、空文件名，消除 gzip 文件头时间及路径差异。

所分发 gzip 约 **28.6 MB**，解压后的只读索引数据库约 **70.9 MB**。英文和中文是原始词典数据，部分词条可能简略、陈旧或存在上游错误；词典并非每个词都自带例句或搭配。AI 补充内容应与离线词典释义分别标示来源。

## 重新构建

在项目根目录使用 Python 3.10+ 执行，脚本仅依赖标准库：

```powershell
python scripts/build_dictionary.py --source ../../work/ecdict/ecdict.csv --download
```

已有固定版本 CSV 时可省略 `--download`；脚本会核验固定 SHA-256，不接受其他版本的 CSV。默认输出 `backend/data_assets/dictionary/ecdict.sqlite3.gz` 和 `manifest.json`，可使用 `--output` 指定另一个输出目录比较结果。

相同源 CSV 与相同 SQLite 版本可重复生成相同的数据库及 gzip 字节。本次构建使用 SQLite **3.53.1**；其他 SQLite 版本可能生成不同页布局及文件哈希，但逻辑词条相同。升级来源时应同时核对上游许可证、固定提交、CSV 哈希及本文，并重新运行测试。

## 本机缓存和查询

首次有效查词时，模块将分发资产流式解压到应用存储目录下 `dictionary-cache/<数据库SHA-256>.sqlite3`；这只是可重建的公共词典缓存，**不是用户词库，不包含密钥、墨墨记录或其他个人数据**。解压有线程锁、唯一临时文件、字节数及哈希验证，完成后原子替换，避免半成品被查询。损坏缓存可从捆绑资产重建。

运行时通过 `mode=ro&immutable=1` 的参数化 SQLite 查询读取，关闭 mmap，页缓存限制为 2 MiB，另缓存最多 512 个原始查询结果，避免一次性解析几十万条记录。每次返回新建的义项和词形列表，调用方无法修改缓存。精确拼写优先于词元；仅在精确拼写缺失时使用项目原有归一化规则回退，不进行模糊拼写猜测。

测试在临时目录中验证真实多义词、词形、精确命中顺序、缓存并发及损坏处理、只读索引和构建可重复性：

```powershell
python -m pytest backend/tests/test_offline_dictionary.py -q
```
