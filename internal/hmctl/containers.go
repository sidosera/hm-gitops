package hmctl

import "sort"

type Object = map[string]any

func AsObject(v any) Object            { m, _ := v.(map[string]any); return m }
func List(m Object, key string) []any  { a, _ := m[key].([]any); return a }
func Text(m Object, key string) string { s, _ := m[key].(string); return s }

// Copy YAML/JSON containers without converting unknown YAML numbers to strings.
func cloneValue(value any) any {
	switch v := value.(type) {
	case map[string]any:
		result := make(map[string]any, len(v))
		for k, item := range v {
			result[k] = cloneValue(item)
		}
		return result
	case map[any]any:
		result := make(map[any]any, len(v))
		for k, item := range v {
			result[k] = cloneValue(item)
		}
		return result
	case []any:
		result := make([]any, len(v))
		for i, item := range v {
			result[i] = cloneValue(item)
		}
		return result
	default:
		return value
	}
}
func Clone(m Object) Object {
	if m == nil {
		return nil
	}
	return cloneValue(m).(map[string]any)
}

func SortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
