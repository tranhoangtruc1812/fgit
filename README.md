# fgit

`fgit` là một công cụ CLI viết bằng Python giúp quản lý workflow Git trên nhiều repository. Repo `root` là meta-repo thuần, chứa manifest mô tả các repo con. Khi clone `root`, `fgit` sẽ tự động clone toàn bộ các repo con về cùng cấp (sibling).

## Cấu trúc

```text
root/                 # meta-repo
├── bin/fgit          # entry point
├── fgit/             # Python package
├── .fgit/manifest.json
└── pyproject.toml

stay-nest/            # repo con (sibling với root)
stay-nest-admin/
stay-nest-booking/
...
```

## Cài đặt

Chọn một trong ba cách:

### A. Symlink

```bash
ln -s /path/to/root/bin/fgit ~/.local/bin/fgit
```

### B. Thêm vào PATH

```bash
export PATH="/path/to/root/bin:$PATH"
```

Thêm dòng trên vào `~/.bashrc` hoặc `~/.zshrc`.

### C. pip install -e

```bash
pip install -e /path/to/root
```

Sau đó gọi `fgit` từ bất kỳ đâu.

## Khởi tạo manifest

Nếu bạn đã có các repo con sẵn ở cùng cấp với `root`:

```bash
cd root
fgit init
```

`fgit init` sẽ quét các thư mục sibling có `.git` và tạo `.fgit/manifest.json`. Nếu manifest đã tồn tại, `fgit init` sẽ so sánh với các sibling repo thực tế:

- Repo mới chưa có trong manifest sẽ được cảnh báo, dùng `--add-missing` để tự động thêm:
  ```bash
  fgit init --add-missing
  ```
- Repo đã xóa khỏi disk nhưng còn trong manifest sẽ được cảnh báo.

## Các lệnh

```bash
fgit clone <root-url> [dest]     # clone root + tất cả repo con (hiển thị cây thư mục sau khi xong)
fgit status                       # trạng thái tất cả repo
fgit pull [--dry-run]            # pull tất cả repo
fgit push [--dry-run]            # push tất cả repo
fgit sync [--dry-run]            # pull rồi push tất cả repo
fgit checkout <branch> [--dry-run] # checkout branch trên root + repo con
fgit branch list                  # liệt kê branch hiện tại
fgit branch create <name> [--dry-run]  # tạo branch trên tất cả repo
fgit branch delete <name> [--dry-run]  # xóa branch trên tất cả repo
fgit exec <command>               # chạy lệnh trong tất cả repo con
fgit sync-from <branch> [--dry-run] # checkout branch, pull latest, quay lại current branch, merge branch vào
fgit doctor                       # kiểm tra sức khỏe các repo (clone, remote, branch, dirty)
fgit explain <command>            # giải thích một lệnh (ví dụ: fgit explain sync-from)
fgit credential set                # lưu username/password GitLab cho HTTPS
fgit credential show               # hiển thị credentials đã lưu
fgit credential encrypt            # mã hóa file credentials bằng GPG
fgit ssh-key generate               # tạo SSH key mới và đăng ký vào ~/.ssh/config
fgit ssh-key show                   # hiển thị public key đã tạo
fgit remote use-https              # chuyển tất cả remote URL sang HTTPS
fgit clean                         # xem file untracked sẽ bị xóa (dry-run)
fgit clean --force                 # xóa thật file untracked
fgit clean --force --ignored       # xóa thật cả file untracked và ignored
fgit project list                  # liệt kê các project đã đăng ký
fgit project add <name> <path>    # thêm project
fgit project use <name>            # chọn project mặc định
fgit config root [path]            # set/get default root (legacy)
```

## Dọn dẹp untracked files

```bash
fgit clean                          # xem trước (dry-run)
fgit clean -f                       # xóa thật
fgit clean -f -x                    # xóa cả ignored files
```

Mặc định `fgit clean` chỉ hiển thị những gì sẽ bị xóa, không xóa thật.

## Cấu hình HTTPS credentials

Vì GitLab không cho phép SSH, `fgit` hỗ trợ xác thực HTTPS bằng username/password.

### 1. Chuyển remote URL sang HTTPS

```bash
fgit remote use-https
```

### 2. Lưu credentials

```bash
fgit credential set
```

Sau đó nhập username và password GitLab. Credentials được lưu tại `~/.config/fgit/netrc`.

### 3. Mã hóa credentials (tùy chọn)

```bash
fgit credential encrypt
```

File sẽ được mã hóa thành `~/.config/fgit/netrc.gpg`. Khi cần sửa:

```bash
fgit credential decrypt
```

### 4. Chạy các lệnh git

```bash
fgit pull
fgit push
fgit sync
fgit sync-from dev
```

Các lệnh này sẽ tự động dùng credentials từ `~/.config/fgit/netrc`.

## Tạo SSH key

Nếu bạn muốn dùng SSH thay vì HTTPS để xác thực với Git host:

```bash
fgit ssh-key generate --host gitlab.atomsolution.vn
```

Lệnh này sẽ:
1. Tạo cặp key `ed25519` tại `~/.ssh/id_ed25519_fgit_<host>` (dùng `--type rsa` nếu cần RSA).
2. Thêm một `Host` entry vào `~/.ssh/config` trỏ tới key vừa tạo (không ghi đè nếu đã tồn tại).
3. In ra public key để bạn copy và thêm vào Git host (ví dụ: GitLab > Settings > SSH Keys).

Dùng `--force` để tạo lại key nếu đã tồn tại. Xem lại public key bất kỳ lúc nào bằng:

```bash
fgit ssh-key show --host gitlab.atomsolution.vn
```

## Quản lý nhiều project

`fgit` hỗ trợ đăng ký nhiều project và chuyển đổi giữa chúng:

```bash
# Đăng ký project
fgit project add atom /home/thtruc/atom/digi-hotel/root
fgit project add retail /home/thtruc/retail/retailx-meta

# Liệt kê project
fgit project list

# Chọn project active
fgit project use atom

# Xem project đang active
fgit project current
```

Sau khi `fgit project use <name>`, mọi lệnh `fgit` gọi từ bất kỳ đâu sẽ tự động dùng root của project đó.

Thứ tự ưu tiên khi tìm root:
1. `fgit -r /path/to/root ...`
2. `FGIT_ROOT` env var
3. Tự động tìm từ cwd lên
4. `active_project` trong config
5. `default_root` trong config (legacy)

## Ví dụ `fgit sync-from`

```bash
# Đang ở nhánh dev/feature trên các repo con
fgit sync-from dev
```

Flow thực hiện trên mỗi repo con:
1. Lưu nhánh hiện tại
2. `git fetch origin`
3. `git checkout <branch>` và `git pull origin <branch>`
4. Quay lại nhánh cũ
5. `git merge <branch>`

Nếu có conflict, merge sẽ bị abort và repo đó được báo lỗi; các repo khác vẫn tiếp tục.

Bình thường `fgit` tự động tìm root bằng cách đi lên từ thư mục hiện tại. Nếu bạn muốn gọi `fgit` từ bất kỳ đâu, hãy set default root:

```bash
fgit config root /path/to/root
```

Hoặc dùng biến môi trường:

```bash
export FGIT_ROOT=/path/to/root
fgit status
```

Hoặc ghi đè tạm thời:

```bash
fgit -r /path/to/root status
```

Ví dụ `fgit exec`:

```bash
fgit exec npm install
fgit exec -- git log --oneline -5
```

## Kiểm tra sức khỏe repo

`fgit doctor` giúp phát hiện các vấn đề phổ biến trong toàn bộ repo con:

- Repo chưa được clone hoặc không phải git repo
- Remote URL không khớp với manifest
- Đang ở nhánh khác với `default_branch` (mặc định: `dev`)
- Working tree đang dirty

```bash
fgit doctor
```

## Giải thích lệnh

Nếu bạn không chắc một lệnh làm gì, dùng `fgit explain`:

```bash
fgit explain sync-from
fgit explain doctor
```

## Dry-run cho lệnh nguy hiểm

Các lệnh có thể thay đổi trạng thái đều hỗ trợ `--dry-run` để xem trước những gì sẽ xảy ra:

```bash
fgit sync-from dev --dry-run
fgit pull --dry-run
fgit push --dry-run
fgit checkout feature-x --dry-run
fgit branch create feature-x --dry-run
```

## Cấu hình

Sửa file `.fgit/manifest.json` để thêm/xóa repo con:

```json
{
  "version": "1.0",
  "default_branch": "dev",
  "layout": "sibling",
  "repos": [
    {"name": "stay-nest", "url": "git@gitlab.atomsolution.vn:pms/stay-nest.git", "branch": "dev"}
  ]
}
```

## Lưu ý

- `root` không chứa code của các repo con; các repo con được `.gitignore`.
- Mặc định nhánh là `dev`.
- Các thao tác trên repo con chạy song song để tăng tốc độ.
