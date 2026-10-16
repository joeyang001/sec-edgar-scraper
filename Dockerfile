FROM apify/actor-python:3.13

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN python3 -m compileall -q main.py

CMD ["python3", "-m", "main"]
