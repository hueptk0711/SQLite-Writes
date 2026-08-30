Offsets follow Python slicing exactly.

start_char is inclusive.
end_char is exclusive.

The selected text is exactly:

Q[start_char:end_char]

If a value occupies character positions i through j inclusive:
start_char = i
end_char = j + 1.

Before returning JSON, verify that Q[start_char:end_char] is exactly one complete atomic database value and contains no surrounding punctuation or field label.
