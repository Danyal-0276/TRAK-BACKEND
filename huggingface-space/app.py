"""
Gradio demo for daniB2112/bart-news-summarizer — deploy as a Hugging Face Space (SDK: Gradio).
"""

import gradio as gr
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL_ID = "daniB2112/bart-news-summarizer"

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = model.to(device)
model.eval()


def summarize(article_text: str, max_new_tokens: int = 128) -> str:
    text = (article_text or "").strip()
    if not text:
        return "Paste an article to summarize."
    inputs = tokenizer(
        text[:4000],
        max_length=1024,
        truncation=True,
        return_tensors="pt",
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=int(max_new_tokens),
            num_beams=4,
            length_penalty=1.0,
            early_stopping=True,
        )
    return tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()


demo = gr.Interface(
    fn=summarize,
    inputs=[
        gr.Textbox(label="Article text", lines=12, placeholder="Paste news article body…"),
        gr.Slider(32, 256, value=128, step=8, label="Max summary tokens"),
    ],
    outputs=gr.Textbox(label="Summary", lines=6),
    title="TRAK BART News Summarizer",
    description=f"Model: [{MODEL_ID}](https://huggingface.co/{MODEL_ID})",
)

if __name__ == "__main__":
    demo.launch()
