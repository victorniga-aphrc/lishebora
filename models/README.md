# ML artifacts

The active pipeline uses the multi-head foodclasses model for
`class_name`, `subclass_name`, and `nova` prediction from product text.

## Active artifact

- `foodclasses_model.pkl` (multi-head BiLSTM)
- `tokenizer.pkl` (training tokenizer)
- `label_encoders.pkl` (class/subclass/nova label decoding)

## Notes

- The standalone NOVA runtime path was removed from the app pipeline.
- Any old NOVA-only helper scripts/docs are deprecated unless reintroduced intentionally.
