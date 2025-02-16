# telegram-gifts-parser
данный проект собирает данные о floor-ценах и истории продаж telegram gifts в реальном времени

инструкция для запуска:

загрузка истории чатов:
скачай историю чатов из следующих каналов в формате json:

уведомления о подарках
изменения floor-цен
основные скрипты:

main.py – собирает два json-файла в базу данных.
⚠️ возможна ошибка с форматом даты, тогда придется вручную подкорректировать формат.
analyzer_v2.py – telegram-бот для анализа подарков, использует собственную базу данных пользователей.
snifer.py – требует telegram-аккаунт для сбора данных в реальном времени и добавления их в базу.
проект сделан «на коленке», но вполне рабочий и поможет лучше анализировать рынок telegram-подарков.

![image](https://github.com/user-attachments/assets/0434a5c5-c5af-4272-afe6-85fb6e37e2e0)

![image](https://github.com/user-attachments/assets/c6148569-e5cf-4f8c-8467-3f0449e8c0f1)

![image](https://github.com/user-attachments/assets/d979a1b7-814f-447d-80b4-162a6c2e5a5b)

![image](https://github.com/user-attachments/assets/b6134b50-726f-453a-9897-995951e13842)
