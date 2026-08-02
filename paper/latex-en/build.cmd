@echo off
rem Сборка PDF препринта. Два прохода pdflatex: второй нужен для оглавления
rem и стабилизации позиций плавающих таблиц. bibtex не вызывается: список
rem литературы свёрстан текстом, references.bib — параллельный артефакт.
cd /d "%~dp0"
pdflatex -interaction=nonstopmode main.tex || exit /b 1
pdflatex -interaction=nonstopmode main.tex || exit /b 1
echo Сборка завершена: main.pdf
