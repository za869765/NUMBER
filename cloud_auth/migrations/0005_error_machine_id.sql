-- v1.0.54：error_reports 加機器識別欄位（讓 admin 一眼看出哪台電腦/誰用的）
ALTER TABLE error_reports ADD COLUMN hostname TEXT DEFAULT '';
ALTER TABLE error_reports ADD COLUMN win_user TEXT DEFAULT '';
ALTER TABLE error_reports ADD COLUMN mac TEXT DEFAULT '';
ALTER TABLE error_reports ADD COLUMN os_ver TEXT DEFAULT '';
