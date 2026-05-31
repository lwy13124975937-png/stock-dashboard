# Streamlit Community Cloud 上云步骤

这份说明给非技术用户照着做。目标是：代码放到 GitHub 私有仓库，真实持仓不进 Git；部署后在网页里手动录入持仓。

参考官方文档：

- Streamlit Community Cloud 可以连接 GitHub 的公开或私有仓库。
- 在 share.streamlit.io 里点 `Create app`，选择仓库、分支和入口文件即可部署。
- 私有仓库部署的应用默认按权限控制，也可以在 App settings -> Sharing 里选择 `Only specific people can view this app`。

## 1. 上传前先确认

项目里这些文件不要上传到 GitHub：

- `holdings_data.json`：真实持仓，包含成本、份额、市值、收益。
- `stock_data.db`：本地数据库缓存。
- `backups/`：历史备份。
- `optional/notify_config.json`：提醒配置。
- `__pycache__/`：Python 缓存。

这些已写进 `.gitignore`。

## 2. 建 GitHub 私有仓库

1. 打开 GitHub。
2. 新建仓库，名字可叫 `stock-dashboard`。
3. Repository visibility 选 `Private`。
4. 不要勾选添加 `README`、`.gitignore` 或 `license`，保持空仓库，避免第一次推送冲突。
5. 建好后，复制 GitHub 显示的仓库地址，例如：

```text
https://github.com/你的用户名/stock-dashboard.git
```

6. 如果电脑已安装 Git，在 `C:\stock` 目录执行：

```powershell
git init
git add .
git commit -m "Initial stock dashboard"
git branch -M main
git remote add origin https://github.com/你的用户名/stock-dashboard.git
git push -u origin main
```

7. 推送时 GitHub 现在不能用账号密码登录，需要用 Personal Access Token。
8. 上传后再看一眼：仓库里不应该出现 `holdings_data.json` 和 `stock_data.db`。

## 3. 部署到 Streamlit Community Cloud

1. 打开 `https://share.streamlit.io`。
2. 用 GitHub 登录。
3. 第一次使用时，允许 Streamlit 访问你的 GitHub 仓库；如果是私有仓库，需要授权访问 private repositories。
4. 点右上角 `Create app`。
5. 选择 `Yup, I have an app`。
6. Repository 选择你的私有仓库。
7. Branch 选择 `main`。
8. Main file path 填：

```text
stock_app.py
```

9. 打开 Advanced settings / Secrets，填入：

```toml
HOLDINGS_DATA_JSON = '''把 holdings_data.json 的完整内容粘贴到这里'''
```

10. 点 `Deploy`。
11. 等几分钟，页面会生成一个 `streamlit.app` 网址。

## 4. 设置仅自己可见

1. 进入 Streamlit 应用页面。
2. 打开 App settings。
3. 找到 Sharing。
4. 选择 `Only specific people can view this app`。
5. 只添加你自己的邮箱，或你允许查看的人。

注意：Streamlit Community Cloud 私有应用数量可能有限制；如果平台提示限制，以页面提示为准。

## 5. 首次部署后录入持仓

因为 `holdings_data.json` 不上传，云端第一次打开时会是空持仓，这是正常的。

1. 打开你的 Streamlit 页面。
2. 进入 `高级功能`。
3. 选择 `持仓管理`。
4. 按账户进入：
   - 银河证券
   - 东方财富
   - 支付宝
5. 点 `添加一只到 xxx`。
6. 选择资产类型：
   - A股个股
   - 场内基金/LOF
   - 场外基金
7. 填名称、代码、成本价、份额；场外基金填当前市值和持有收益。
8. 点 `添加并保存`。
9. 系统会自动写入云端的 `holdings_data.json`，并备份旧版本。

## 6. 重要限制

- Streamlit Community Cloud 不适合做真正的每日定时任务。
- 如果要每个交易日收盘后自动跑 `update_data.py`，更适合用云服务器或 GitHub Actions。
- 当前页面打开时会读取行情；板块长期情绪需要 `board_heat` 连续多日数据，否则会显示“历史不足”。
- Community Cloud 的运行环境可能因重启或重新部署丢失运行时写入的文件；录入持仓后要定期在“高级功能”里检查，长期稳定方案仍建议后续换云服务器或外部数据库。

## 7. GitHub Actions 每日快照怎么用

项目里已经有 `.github/workflows/daily.yml`。它会在周一到周五北京时间约 15:40 自动运行一次：安装依赖、读取加密持仓 Secret、运行 `daily_snapshot.py`，再把 `history/` 里的收益历史和板块历史提交回仓库。

因为 `holdings_data.json` 不能上传到 GitHub，所以要把它放进 GitHub Secret：

1. 在自己电脑打开 `holdings_data.json`。
2. 全选里面的内容并复制。
3. 打开 GitHub 仓库页面。
4. 进入 `Settings` -> `Secrets and variables` -> `Actions`。
5. 点 `New repository secret`。
6. Name 填：

```text
HOLDINGS_DATA_JSON
```

7. Secret 内容粘贴刚才复制的 JSON。
8. 点保存。

还要打开自动提交权限：

1. GitHub 仓库进入 `Settings`。
2. 点左侧 `Actions` -> `General`。
3. 找到 `Workflow permissions`。
4. 选择 `Read and write permissions`。
5. 点 `Save`。

以后自动任务生成的是 `history/snapshots.csv` 和 `history/board_heat.csv`，这两个文件会提交到 Git；真实持仓文件 `holdings_data.json` 仍然不会提交。

## 8. Personal Access Token 怎么生成

GitHub 推送代码时如果弹出登录框，密码位置不要填 GitHub 密码，要填 Token。

1. GitHub 右上角头像 -> `Settings`。
2. 左侧最下面 `Developer settings`。
3. `Personal access tokens` -> `Tokens (classic)`。
4. 点 `Generate new token`。
5. 勾选 `repo` 权限。
6. 生成后马上复制保存；这个 token 只显示一次。
7. 推送代码时，用户名填 GitHub 用户名，密码填这个 token。

## 9. 手机怎么看

1. Streamlit 部署成功后，复制 `.streamlit.app` 网址。
2. 用手机浏览器打开。
3. 登录允许访问的人对应的账号。
4. 添加到浏览器书签或手机桌面。
5. 免费版长时间不用会休眠，第一次打开等十几秒是正常的。
