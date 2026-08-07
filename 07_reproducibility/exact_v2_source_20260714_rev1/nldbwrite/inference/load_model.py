def load_local_model(model_name_or_path, load_in_4bit=True, torch_dtype='float16', device_map='auto', revision='main'):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tokenizer=AutoTokenizer.from_pretrained(model_name_or_path, revision=revision, trust_remote_code=True)
    quant_config=None
    if load_in_4bit:
        quant_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True)
    dtype=torch.float16 if torch_dtype=='float16' else torch.bfloat16 if torch_dtype=='bfloat16' else torch.float32
    model=AutoModelForCausalLM.from_pretrained(model_name_or_path, revision=revision, device_map=device_map, torch_dtype=dtype, quantization_config=quant_config, trust_remote_code=True)
    model.eval(); return tokenizer, model

def generate_text(
    tokenizer,
    model,
    prompt,
    max_new_tokens=1024,
    temperature=0.0,
    top_p=1.0,
    num_return_sequences=1,
    seed=42,
):
    import torch
    from transformers import set_seed
    messages=[{'role':'user','content':prompt}]
    text=tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True) if getattr(tokenizer,'chat_template',None) else prompt
    inputs=tokenizer(text, return_tensors='pt').to(model.device)
    temperature = float(temperature or 0.0)
    top_p = float(top_p or 1.0)
    num_return_sequences = int(num_return_sequences or 1)
    do_sample = temperature > 0.0
    if not do_sample and num_return_sequences > 1:
        raise ValueError('num_return_sequences > 1 requires sampling or beam search.')
    set_seed(int(seed))
    generate_kwargs = {
        **inputs,
        'max_new_tokens': int(max_new_tokens),
        'do_sample': do_sample,
        'num_return_sequences': num_return_sequences,
        'pad_token_id': tokenizer.eos_token_id,
    }
    if do_sample:
        generate_kwargs['temperature'] = temperature
        generate_kwargs['top_p'] = top_p
    with torch.no_grad():
        out = model.generate(**generate_kwargs)
    prompt_len = inputs['input_ids'].shape[-1]
    decoded = [tokenizer.decode(row[prompt_len:], skip_special_tokens=True) for row in out]
    return decoded[0] if num_return_sequences == 1 else decoded
