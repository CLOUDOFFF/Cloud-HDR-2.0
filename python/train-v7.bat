@echo off
chcp 65001 >nul
cd /d "%~dp0"
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set PYTHONIOENCODING=utf-8
set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
"%~dp0..\runtime\python.exe" -m cloudhdr_ai train --data data/ultra-v7.jsonl --resume checkpoints/v7/last.pt --model ai-forever/rugpt3large_based_on_gpt2 --lora --lora-rank 64 --lora-alpha 128 --lr 1.3e-4 --warmup 20 --neftune 5 --dropout 0.05 --batch-size 2 --grad-accum 8 --steps 1000 --eval-interval 200 --save-interval 250 --log-interval 20 --seed 20260924 --out checkpoints/v7 > train-v7.log 2>&1