@echo off
REM 一键打开 Jenkins UI 隧道(测试服)
REM 用法:双击运行,窗口保持开着;然后浏览器访问 http://localhost:8080
REM 登录: admin / 密码在服务器 /root/.jenkins-admin-credentials
echo Jenkins UI 隧道已建立,浏览器访问: http://localhost:8080/jenkins/
echo (也可以直接访问公网地址 http://8.138.111.12/jenkins/ ,无需本窗口)
echo (本窗口保持开着 = 隧道工作中;关闭窗口 = 断开)
ssh -i "%USERPROFILE%\Downloads\doc.pem" -N -L 8080:127.0.0.1:8080 root@8.138.111.12
