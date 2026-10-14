<script setup>
import { computed } from 'vue'
import { authStore } from '../stores/auth'
import Home from './Home.vue'
import Landing from './Landing.vue'

const authed = computed(() => !!authStore.state.user)
</script>

<template>
  <Home v-if="authed" />
  <Landing v-else />
</template>
