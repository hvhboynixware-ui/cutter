# Cutter — установщик (MSI)

WiX v3.11 (portable, без установки и без прав администратора) собирает
`dist\Cutter-<version>-x64.msi` из one-folder сборки PyInstaller `dist\Cutter\`.

## Быстрый старт

```powershell
# из корня репозитория
powershell -ExecutionPolicy Bypass -File installer\build.ps1

# результат
#   dist\Cutter-1.0.0-x64.msi
#   installer\build.log

# проверка установки/удаления без прав администратора
powershell -ExecutionPolicy Bypass -File installer\test-install.ps1
#   installer\test-install.log
#   installer\test-install-msiexec.log
```

Установка вручную (без прав администратора, per-user):

```powershell
msiexec /i "<repo>\dist\Cutter-1.0.0-x64.msi" /qn MSIINSTALLPERUSER=1
```

Обычный запуск с правами администратора: `msiexec /i "<msi>"` — ставится
в `C:\Program Files\Cutter` (per-machine). Удаление — через «Программы и
компоненты» или `msiexec /x "<msi>" /qn`.

## Что где лежит

| Файл | Назначение |
|---|---|
| `Cutter.wxs` | WiX-исходник продукта: каталоги, ярлыки, ARP, MajorUpgrade, per-user fallback |
| `build.ps1` | одна команда: heat + candle + light, лог в `build.log` |
| `test-install.ps1` | реальный тест: install/uninstall/reinstall/upgrade через `msiexec /qn` |
| `icon.ico` | fallback-иконка продукта/ARP (если в `dist\Cutter\icon.ico` нет); генерируется `make-icon.ps1` |
| `make-icon.ps1` | генератор fallback-иконки `icon.ico` (если нет готовой иконки от PyInstaller) |
| `license.rtf` | лицензия для окна установщика (WixUI_Minimal) |
| `Cutter.ru-RU.wxl` | локализация, задаёт code page 1251 для кириллицы в MSI |
| `exclude-runtime.xsl` | XSL-фильтр heat.exe: не тащить в MSI рантайм-хвосты `data\webview2`, `data\logs` |
| `obj\` | рабочие файлы сборки (`Files.wxs` от heat, `.wixobj`) — пересоздаётся |
| `tools\wix\` (в корне репо) | распакованный WiX v3.11 (скачивается build.ps1 при необходимости) |

## Под капотом

1. `heat.exe dir dist\Cutter -cg ProductComponents -dr INSTALLFOLDER -gg -g1 -sreg -scom -srd -var var.SourceDir -t exclude-runtime.xsl`
   → `installer\obj\Files.wxs` (список файлов, Component GUIDs детерминированные).
2. `candle.exe -arch x64` компилирует `Files.wxs` и `Cutter.wxs`.
3. `light.exe -ext WixUIExtension.dll -loc Cutter.ru-RU.wxl -cultures:ru-RU` линкует MSI.

Проверено на текущей сборке `dist\Cutter` (726 МБ, 580 файлов): в MSI попадает
**374 файла / 280,8 МБ**; рантайм-папка `dist\Cutter\data` (профиль WebView2, логи)
исключается фильтром `exclude-runtime.xsl`.

Схема продукта:

* `Version=1.0.0`, `UpgradeCode` фиксированный, `ProductCode="*"` (новый на каждую сборку);
* `<MajorUpgrade AllowSameVersionUpgrades="yes">` — установка поверх и смена версии работают;
* `ALLUSERS=2` — dual-purpose: с правами админа `[ProgramFiles64Folder]\Cutter`,
  без прав — `%LOCALAPPDATA%\Programs\Cutter` (кастомное действие `SetPerUserInstallFolder`
  до `CostFinalize`, ключ реестра через `HKMU`);
* ярлыки: меню Пуск (папка Cutter) и рабочий стол; иконку ярлык берёт из `Cutter.exe`
  (в MSI `Icon` для ярлыков не задан), иконка ARP — `dist\Cutter\icon.ico`, если есть;
* запись в «Программах и компонентах»: `ARPPRODUCTICON`, `ARPCOMMENTS`, `UninstallString`;
* при удалении удаляются файлы, ярлыки, папка Пуск и `Software\Cutter`.

## Обходы ограничений окружения (важно!)

Всё это уже вшито в `build.ps1`, но знать надо:

1. **WiX v3 без прав администратора** — portable zip в `tools\wix`, ставить нечего.
   Нужен только .NET Framework 4.x (на Windows 10/11 он есть).
2. **`light.exe` из пути с кириллицей** не может создать временные файлы
   («Отказано в доступе») — тулчейн копируется в ASCII-каталог
   `%LOCALAPPDATA%\Temp\cutter-wix-toolset` и запускается оттуда.
3. **`light.exe` с не-ASCII `%TEMP%`** зависает на упаковке CAB — `build.ps1`
   переключает `TEMP/TMP` на ASCII-каталог `%LOCALAPPDATA%\Temp\cutter-wix-tmp`.
4. **`WixCreateCab` не умеет писать cab в путь с кириллицей** (`ERROR_INVALID_NAME`) —
   MSI собирается в ASCII-временный путь и потом переносится в `dist\`.
5. **Кириллица в строках MSI** требует code page 1251: задаётся `Cutter.ru-RU.wxl`
   (`-loc` + `-cultures:ru-RU`), иначе LGHT0311 на code page 1252.
6. **`ICE57`** ругается на `HKMU` в dual-purpose пакете (ложное срабатывание),
   **`ICE61`** — на `Maximum version == current` при `AllowSameVersionUpgrades`.
   Оба подавлены через `-sice:` в `build.ps1`.

## Известные ограничения / риски

* **`msiexec /i setup.msi /qn` без параметров** на аккаунте, входящем в группу
  администраторов: Windows Installer выбирает per-machine и валится `1603`
  (нужен UAC). Это штатное поведение dual-purpose пакета. Для установки без
  прав явно передавайте `MSIINSTALLPERUSER=1` (так делает `test-install.ps1`).
  На аккаунте без прав администратора авто-режим уходит в per-user.
* **Запись в «Программах и компонентах»** при silent per-user установке
  создаётся в `HKLM\...\Uninstall\<ProductCode>` (MSI ставит через службу),
  при этом файлы — в профиле пользователя. На однопользовательской машине
  проблемы нет, но запись видна всем пользователям.
* Code page MSI = 1251: путь установки может содержать кириллицу, но не
  произвольный Unicode (например, китайские имена папок не установятся).
* **WebView2 Runtime** установщиком не ставится — предполагается, что он уже
  есть в системе (Evergreen Runtime, обычно предустановлен на Windows 10/11).
  Если нужен офлайн-инсталлятор WebView2 — это отдельная задача.
* Иконка ARP берётся из `dist\Cutter\icon.ico` (та же, что у приложения);
  `installer\icon.ico` — только fallback, если в dist иконки нет (тогда
  генерируется `make-icon.ps1`, тёмный квадрат с «C»).
* Мажорный апгрейд тестируется `test-install.ps1 -UpgradeMsi <новый msi>`:
  ставит старую версию, затем новую, проверяет, что старая снята и в ARP одна запись.
