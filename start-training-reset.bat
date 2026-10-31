@echo off
rem Starts the TRAINING copy from scratch. The old training data is kept in a folder
rem named training-data-previous-<time>. The real shop data is never touched.
call "%~dp0start.bat" training reset
