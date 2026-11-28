package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"
)

type workspaceActivity struct {
	ClipsBusy       bool `json:"clips_busy"`
	TranscriptsBusy bool `json:"transcripts_busy"`
	ScriptsBusy     bool `json:"scripts_busy"`
	Jobs            []struct{ State string }
}

func (workspace workspaceActivity) busy(keepPaused bool) bool {
	if workspace.ClipsBusy || workspace.TranscriptsBusy || workspace.ScriptsBusy {
		return true
	}
	for _, job := range workspace.Jobs {
		switch job.State {
		case "queued", "downloading", "processing", "publishing", "canceling":
			return true
		case "paused":
			if keepPaused {
				return true
			}
		}
	}
	return false
}

func readWorkspaceActivity(client *http.Client, request *http.Request) (workspaceActivity, error) {
	var workspace workspaceActivity
	response, err := client.Do(request)
	if err != nil {
		return workspace, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return workspace, fmt.Errorf("workspace status: %s", response.Status)
	}
	err = json.NewDecoder(response.Body).Decode(&workspace)
	return workspace, err
}

func waitForBackgroundTasks(ctx context.Context, url string, exited <-chan struct{}, keepPaused bool, notify func()) {
	client := &http.Client{Timeout: 4 * time.Second}
	defer client.CloseIdleConnections()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url+"api/workspace", nil)
	if err != nil {
		return
	}
	request.Header.Set("X-Videoport", "1")
	ticker := time.NewTicker(4 * time.Second)
	defer ticker.Stop()
	for {
		workspace, err := readWorkspaceActivity(client, request)
		if err != nil {
			if ctx.Err() == nil {
				log.Print(err)
			}
		} else {
			if !workspace.busy(keepPaused) {
				return
			}
			if notify != nil {
				notify()
				notify = nil
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-exited:
			return
		case <-ticker.C:
		}
	}
}
