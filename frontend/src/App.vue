<template>
  <div class="app-root" :class="{ 'is-login': route.path === '/login' }">
    <router-view />
  </div>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>
