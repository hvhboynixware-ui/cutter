<?xml version="1.0" encoding="utf-8"?>
<!--
  XSL-трансформ для heat.exe: выкидывает из MSI рантайм-мусор, который
  появляется в dist\Cutter после тестовых запусков приложения:
    data\logs\*, data\webview2\* (профиль Edge WebView2), data\jobs\*,
    data\shots\*, data\browser_profiles\* и т.п.
  Само приложение (frozen) пишет пользовательские данные в %LOCALAPPDATA%\Cutter,
  поэтому эта папка в установке не нужна.
  Пустые Directory-элементы остаются — установщик просто создаст пустые папки.
-->
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:wix="http://schemas.microsoft.com/wix/2006/wi"
                exclude-result-prefixes="wix">
  <xsl:output method="xml" indent="yes" encoding="utf-8" omit-xml-declaration="no" />
  <xsl:strip-space elements="*" />

  <!-- copy everything by default -->
  <xsl:template match="@*|node()">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()" />
    </xsl:copy>
  </xsl:template>

  <!-- выкинуть компоненты, чьи файлы лежат в dist\Cutter\data\... -->
  <xsl:template match="wix:Component[starts-with(wix:File/@Source, '$(var.SourceDir)\data\')]" />

  <!-- per-user ICE38: KeyPath переносим с File на HKCU-реестр -->
  <xsl:template match="wix:Component">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()" />
      <wix:RegistryValue Root="HKCU" Key="Software\Cutter\Components" Name="{@Id}" Type="string" Value="1" KeyPath="yes" />
    </xsl:copy>
  </xsl:template>

  <xsl:template match="wix:File/@KeyPath" />

</xsl:stylesheet>
