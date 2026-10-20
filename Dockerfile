FROM python:3.12-slim
RUN pip install --no-cache-dir limnoria pytz num2words
# plugin nella dir dei plugins extra
RUN mkdir -p /plugins/Tripsit
COPY plugin.py config.py __init__.py README.md test.py /plugins/Tripsit/
RUN useradd -m bot
USER bot
WORKDIR /home/bot
VOLUME /home/bot/data
CMD ["supybot", "/home/bot/data/tripsit.conf"]
