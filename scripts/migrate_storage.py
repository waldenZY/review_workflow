"""Retired entry point. Never opens or modifies a database."""
import sys

if __name__ == '__main__':
    sys.exit('不支持迁移旧数据。请停服、备份并自行清空业务数据目录，再启动存储修订 4。此命令未读取或修改数据库。')
