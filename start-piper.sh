docker run -d --name wyoming-piper --network tts-net --restart unless-stopped -p 10200:10200 -v ~/.piper-models:/data rhasspy/wyoming-piper --voice en_GB-cori-medium
