from openai import OpenAI
import os
import base64
from mini_bdx_runtime.camera import Cam

cam = Cam()

client = OpenAI()

def encode_image(image_path: str):
    # check if the image exists
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

# response = client.responses.create(
#     model="gpt-4o-mini",
#     input=[{
#         "role": "user",
#         "content": [
#             {"type": "input_text", "text": "what's in this image?"},
#             {
#                 "type": "input_image",
#                 "image_url": "http://s-nguyen.net:4444/images/aze.jpg",
#             },
#         ],
#     }],
# )

# image64 = encode_image("/home/antoine/aze.jpg")
image64 = cam.get_encoded_image()
response = client.responses.create(
    model="gpt-4o-mini",
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "what's in this image?"},
            {
                "type": "input_image",
                "image_url": "data:image/jpeg;base64,"+image64,
            },
        ],
    }],
)

print(response.output_text)
