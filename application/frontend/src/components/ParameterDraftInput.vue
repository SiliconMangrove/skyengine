<template>
  <textarea
    v-if="multiline"
    v-model="draft"
    @input="emit('input')"
    @change="emit('change', $event)"
  ></textarea>
  <input
    v-else
    v-model="draft"
    :type="type"
    @input="emit('input')"
    @change="emit('change', $event)"
  />
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  value: { type: [String, Number], default: '' },
  type: { type: String, default: 'text' },
  multiline: { type: Boolean, default: false },
})
const emit = defineEmits(['input', 'change'])
/** @type {import('vue').Ref<string | number>} */
const draft = ref(props.value)

// Keep unfinished input across parent polling renders; accept actual value changes.
watch(() => props.value, value => { draft.value = value })
</script>
